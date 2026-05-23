"""SQLite-backed implementation of the GraphStore interface."""

from __future__ import annotations

import json
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

from corbell.core.graph.schema import (
    DataStoreNode,
    DependencyEdge,
    FlowNode,
    GraphStore,
    MethodNode,
    QueueNode,
    ServiceNode,
)

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS graph_nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL,
    data TEXT NOT NULL
);
"""

_CREATE_EDGES = """
CREATE TABLE IF NOT EXISTS graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_id, target_id, kind)
);
"""

_CREATE_IDX_SOURCE = "CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(source_id);"
_CREATE_IDX_TARGET = "CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(target_id);"


def _node_to_dict(node: ServiceNode | DataStoreNode | QueueNode | MethodNode) -> dict:
    """Serialize any node dataclass to a plain dict."""
    from dataclasses import asdict
    d = asdict(node)
    # Convert lists inside fields
    for k, v in d.items():
        if isinstance(v, Path):
            d[k] = str(v)
    return d


def _dict_to_node(node_type: str, data: dict) -> ServiceNode | DataStoreNode | QueueNode | MethodNode | FlowNode:
    """Deserialize a dict back to a typed node dataclass."""
    if node_type == "service":
        return ServiceNode(**{k: v for k, v in data.items() if k in ServiceNode.__dataclass_fields__})
    if node_type == "datastore":
        return DataStoreNode(**data)
    if node_type == "queue":
        return QueueNode(**data)
    if node_type == "method":
        return MethodNode(**{k: v for k, v in data.items() if k in MethodNode.__dataclass_fields__})
    if node_type == "flow":
        return FlowNode(**{k: v for k, v in data.items() if k in FlowNode.__dataclass_fields__})
    raise ValueError(f"Unknown node_type: {node_type}")


def _node_type_str(node) -> str:
    if isinstance(node, ServiceNode):
        return "service"
    if isinstance(node, DataStoreNode):
        return "datastore"
    if isinstance(node, QueueNode):
        return "queue"
    if isinstance(node, MethodNode):
        return "method"
    if isinstance(node, FlowNode):
        return "flow"
    raise TypeError(f"Unsupported node type: {type(node)}")


class SQLiteGraphStore(GraphStore):
    """Graph store backed by a local SQLite database.

    Creates two tables: ``graph_nodes`` and ``graph_edges``. All node data is
    stored as JSON blobs for schema flexibility.
    """

    def __init__(self, db_path: Path | str):
        """Initialize the store, creating the database file if needed.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(_CREATE_NODES)
            conn.execute(_CREATE_EDGES)
            conn.execute(_CREATE_IDX_SOURCE)
            conn.execute(_CREATE_IDX_TARGET)
            conn.commit()

    def upsert_node(self, node) -> None:
        """Insert or update a node."""
        node_type = _node_type_str(node)
        data = _node_to_dict(node)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO graph_nodes (id, node_type, data) VALUES (?, ?, ?)",
                (node.id, node_type, json.dumps(data)),
            )
            conn.commit()

    def upsert_edge(self, edge: DependencyEdge) -> None:
        """Insert or update an edge (unique on source+target+kind)."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO graph_edges (source_id, target_id, kind, metadata)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source_id, target_id, kind)
                   DO UPDATE SET metadata = excluded.metadata""",
                (edge.source_id, edge.target_id, edge.kind, json.dumps(edge.metadata)),
            )
            conn.commit()

    def _load_node(self, row) -> ServiceNode | DataStoreNode | QueueNode | MethodNode:
        return _dict_to_node(row["node_type"], json.loads(row["data"]))

    def get_service(self, service_id: str) -> Optional[ServiceNode]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM graph_nodes WHERE id = ? AND node_type = 'service'",
                (service_id,),
            ).fetchone()
            if row:
                return self._load_node(row)
        return None

    def get_all_services(self) -> List[ServiceNode]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM graph_nodes WHERE node_type = 'service'"
            ).fetchall()
            return [self._load_node(r) for r in rows]

    def get_dependencies(self, service_id: str) -> List[DependencyEdge]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM graph_edges WHERE source_id = ?", (service_id,)
            ).fetchall()
            return [
                DependencyEdge(
                    source_id=r["source_id"],
                    target_id=r["target_id"],
                    kind=r["kind"],
                    metadata=json.loads(r["metadata"]),
                )
                for r in rows
            ]

    def get_dependents(self, service_id: str) -> List[DependencyEdge]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM graph_edges WHERE target_id = ?", (service_id,)
            ).fetchall()
            return [
                DependencyEdge(
                    source_id=r["source_id"],
                    target_id=r["target_id"],
                    kind=r["kind"],
                    metadata=json.loads(r["metadata"]),
                )
                for r in rows
            ]

    def get_method(self, method_id: str) -> Optional[MethodNode]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM graph_nodes WHERE id = ? AND node_type = 'method'",
                (method_id,),
            ).fetchone()
            if row:
                return self._load_node(row)
        return None

    def get_call_path(
        self, from_method_id: str, to_method_id: str, max_depth: int = 5
    ) -> List[List[str]]:
        """BFS to find all call paths between two method nodes."""
        paths: List[List[str]] = []
        queue: deque[List[str]] = deque([[from_method_id]])

        with self._conn() as conn:
            while queue:
                path = queue.popleft()
                if len(path) > max_depth:
                    continue
                current = path[-1]
                if current == to_method_id:
                    paths.append(path)
                    continue
                rows = conn.execute(
                    "SELECT target_id FROM graph_edges WHERE source_id = ? AND kind = 'method_call'",
                    (current,),
                ).fetchall()
                for row in rows:
                    neighbor = row["target_id"]
                    if neighbor not in path:  # avoid cycles
                        queue.append(path + [neighbor])
        return paths

    def get_methods_for_service(self, service_id: str) -> List[MethodNode]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM graph_nodes WHERE node_type = 'method'"
            ).fetchall()
            results = []
            for row in rows:
                data = json.loads(row["data"])
                if data.get("service_id") == service_id:
                    results.append(_dict_to_node("method", data))
            return results

    def get_callers_of_method(self, method_id: str) -> List[MethodNode]:
        """Return all MethodNodes that have a method_call edge targeting method_id."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT source_id FROM graph_edges WHERE target_id = ? AND kind = 'method_call'",
                (method_id,),
            ).fetchall()
            caller_ids = [r["source_id"] for r in rows]

        result: List[MethodNode] = []
        with self._conn() as conn:
            for cid in caller_ids:
                row = conn.execute(
                    "SELECT * FROM graph_nodes WHERE id = ? AND node_type = 'method'",
                    (cid,),
                ).fetchone()
                if row:
                    result.append(self._load_node(row))  # type: ignore[arg-type]
        return result

    def get_flows_for_method(self, method_id: str) -> List[dict]:
        """Return flows that include method_id as a step.

        Each returned dict has keys: ``flow_id``, ``flow_name``, ``step``,
        ``entry_method_id``.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT source_id, metadata FROM graph_edges "
                "WHERE target_id = ? AND kind = 'flow_step'",
                (method_id,),
            ).fetchall()

        result = []
        with self._conn() as conn:
            for row in rows:
                flow_id = row["source_id"]
                meta = json.loads(row["metadata"] or "{}")
                flow_row = conn.execute(
                    "SELECT data FROM graph_nodes WHERE id = ? AND node_type = 'flow'",
                    (flow_id,),
                ).fetchone()
                if flow_row:
                    flow_data = json.loads(flow_row["data"])
                    result.append({
                        "flow_id": flow_id,
                        "flow_name": flow_data.get("name", ""),
                        "step": meta.get("step", 0),
                        "entry_method_id": flow_data.get("entry_method_id", ""),
                    })
        return result

    def get_all_nodes_summary(self) -> Dict[str, Any]:
        with self._conn() as conn:
            node_counts = conn.execute(
                "SELECT node_type, COUNT(*) as cnt FROM graph_nodes GROUP BY node_type"
            ).fetchall()
            edge_count = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        return {
            "nodes": {r["node_type"]: r["cnt"] for r in node_counts},
            "edges": edge_count,
        }

    def to_mermaid(self) -> str:
        """Return a single Mermaid file string describing the system boundaries."""
        with self._conn() as conn:
            rows = conn.execute("SELECT id, node_type, data FROM graph_nodes").fetchall()
            services, datastores, queues = [], [], []

            for row in rows:
                ntype = row["node_type"]
                data = json.loads(row["data"])
                nid = row["id"].replace("-", "_").replace(".", "_").replace(":", "_")
                label = data.get("name", row["id"]).replace('"', "'")
                if ntype == "service":
                    services.append({"id": nid, "label": label})
                elif ntype == "datastore":
                    datastores.append({"id": nid, "label": label})
                elif ntype == "queue":
                    queues.append({"id": nid, "label": label})

            edges = conn.execute("SELECT source_id, target_id, kind FROM graph_edges").fetchall()
            connections = []
            seen = set()
            for e in edges:
                kind = e["kind"]
                if kind in ("method_call", "flow_step", "git_coupling", "flow_link"):
                    continue
                src = e["source_id"].replace("-", "_").replace(".", "_").replace(":", "_")
                tgt = e["target_id"].replace("-", "_").replace(".", "_").replace(":", "_")
                if (src, tgt, kind) in seen:
                    continue
                seen.add((src, tgt, kind))

                if kind == "http_call":
                    connections.append(f"  {src} -- HTTP --> {tgt}")
                elif kind == "rpc_call":
                    connections.append(f"  {src} -- RPC/Edge Function --> {tgt}")
                elif kind == "db_read":
                    connections.append(f"  {src} -- Reads --> {tgt}")
                elif kind == "db_write":
                    connections.append(f"  {src} -- Writes --> {tgt}")
                elif kind == "queue_publish":
                    connections.append(f"  {src} -- Publishes --> {tgt}")
                elif kind == "queue_consume":
                    connections.append(f"  {src} -- Consumes --> {tgt}")
                elif kind == "library_dependency":
                    connections.append(f"  {src} -. Import/Library .-> {tgt}")
                else:
                    connections.append(f"  {src} --> {tgt}")

        lines = ["graph LR"]
        lines.append("  %% Services")
        for s in services:
            lines.append(f'  {s["id"]}["{s["label"]}"]')

        if datastores:
            lines.append("  %% Data Stores")
            for d in datastores:
                lines.append(f'  {d["id"]}[("{d["label"]}")]')

        if queues:
            lines.append("  %% Queues")
            for q in queues:
                lines.append(f'  {q["id"]}>"{q["label"]}"]')

        lines.append("  %% Edges")
        lines.extend(connections)

        lines.append("  %% Styling")
        lines.append("  classDef service fill:#161b22,stroke:#39d353,stroke-width:2px,color:#c9d1d9;")
        lines.append("  classDef datastore fill:#161b22,stroke:#ffa657,stroke-width:2px,color:#c9d1d9;")
        lines.append("  classDef queue fill:#161b22,stroke:#bc8cff,stroke-width:2px,color:#c9d1d9;")

        for s in services:
            lines.append(f'  class {s["id"]} service')
        for d in datastores:
            lines.append(f'  class {d["id"]} datastore')
        for q in queues:
            lines.append(f'  class {q["id"]} queue')

        return "\n".join(lines)

    def to_json(self) -> str:
        """Return the JSON representation of the service graph."""
        nodes = []
        edges = []
        with self._conn() as conn:
            rows = conn.execute("SELECT id, node_type, data FROM graph_nodes").fetchall()
            for row in rows:
                ntype = row["node_type"]
                data = json.loads(row["data"])
                node = {"id": row["id"], "type": ntype}
                if ntype == "service":
                    node.update({
                        "label": data.get("name", row["id"]),
                        "language": data.get("language", ""),
                        "service_type": data.get("service_type", "api"),
                        "tags": data.get("tags", []),
                    })
                elif ntype == "datastore":
                    node.update({"label": data.get("name", row["id"]), "kind": data.get("kind", "")})
                elif ntype == "queue":
                    node.update({"label": data.get("name", row["id"]), "kind": data.get("kind", "")})
                elif ntype == "flow":
                    svc_id = data.get("service_id", "")
                    node.update({
                        "label": data.get("name", row["id"]),
                        "service_id": svc_id,
                        "step_count": data.get("step_count", 0),
                    })
                    edges.append({
                        "source": svc_id,
                        "target": row["id"],
                        "kind": "flow_link",
                        "meta": {}
                    })
                elif ntype == "method":
                    continue  # don't clutter service-level graph
                nodes.append(node)

            # Count methods per service for node sizing
            method_counts: Dict[str, int] = {}
            mcounts = conn.execute(
                "SELECT data FROM graph_nodes WHERE node_type='method'"
            ).fetchall()
            for row in mcounts:
                d = json.loads(row["data"])
                sid = d.get("service_id", "")
                if sid:
                    method_counts[sid] = method_counts.get(sid, 0) + 1
            for n in nodes:
                if n["type"] == "service":
                    n["method_count"] = method_counts.get(n["id"], 0)

            # Edges
            skip_kinds = {"method_call", "flow_step"}
            erows = conn.execute(
                "SELECT source_id, target_id, kind, metadata FROM graph_edges"
            ).fetchall()
            seen = set()
            for row in erows:
                if row["kind"] in skip_kinds:
                    continue
                key = (row["source_id"], row["target_id"], row["kind"])
                if key in seen:
                    continue
                seen.add(key)
                meta = json.loads(row["metadata"] or "{}")
                edges.append({
                    "source": row["source_id"],
                    "target": row["target_id"],
                    "kind": row["kind"],
                    "meta": meta,
                })
        return json.dumps({"nodes": nodes, "edges": edges}, indent=2)

    def clear(self) -> None:
        """Delete all graph data."""
        with self._conn() as conn:
            conn.execute("DELETE FROM graph_nodes")
            conn.execute("DELETE FROM graph_edges")
            conn.commit()
