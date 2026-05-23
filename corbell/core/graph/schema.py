"""Graph schema: nodes, edges, and the GraphStore abstract interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ServiceNode:
    """Represents a discovered service (microservice, API, worker, etc.)."""

    id: str
    name: str
    repo: str
    language: str = "python"
    tags: List[str] = field(default_factory=list)
    service_type: str = "api"  # api | worker | cron | cli | service | infrastructure


@dataclass
class DataStoreNode:
    """Represents a data store (DB, cache, object store)."""

    id: str
    kind: str  # postgres | redis | s3 | kafka | dynamodb | sqlite | mongodb | neo4j | chromadb
    name: str


@dataclass
class QueueNode:
    """Represents a message queue."""

    id: str
    kind: str  # sqs | rabbitmq | kafka | pubsub
    name: str


@dataclass
class MethodNode:
    """Represents a function or method extracted from source code.

    Node ID format: ``{repo}::{file_path}::{class_name}::{method_name}``
    """

    id: str
    repo: str
    file_path: str
    class_name: Optional[str]
    method_name: str
    signature: str
    docstring: Optional[str]
    line_start: int
    line_end: int
    service_id: str
    typed_signature: Optional[str] = None  # e.g. "validate(token: str) -> bool"


@dataclass
class FlowNode:
    """Represents a named execution flow detected from entry points.

    A flow is a BFS-traversal from an entry point (HTTP handler, CLI command,
    worker) through the call graph.  It records which methods participate and
    in which order, enabling Linear tasks to say "step 3 of LoginFlow".

    Node ID format: ``flow::{service_id}::{flow_name}``
    """

    id: str
    name: str          # e.g. "LoginFlow"
    service_id: str
    entry_method_id: str  # method ID of the detected entry point
    step_count: int = 0


@dataclass
class DependencyEdge:
    """A directed relationship between two graph nodes."""

    source_id: str
    target_id: str
    kind: str  # http_call | grpc_call | db_read | db_write | queue_publish | queue_consume | import | method_call
    metadata: Dict[str, Any] = field(default_factory=dict)


class GraphStore(ABC):
    """Abstract interface for the architecture graph store."""

    @abstractmethod
    def upsert_node(self, node: ServiceNode | DataStoreNode | QueueNode | MethodNode | FlowNode) -> None:
        """Insert or update a node in the graph."""
        ...

    @abstractmethod
    def upsert_edge(self, edge: DependencyEdge) -> None:
        """Insert or update an edge in the graph."""
        ...

    @abstractmethod
    def get_service(self, service_id: str) -> Optional[ServiceNode]:
        """Retrieve a service node by ID."""
        ...

    @abstractmethod
    def get_all_services(self) -> List[ServiceNode]:
        """Return all service nodes."""
        ...

    @abstractmethod
    def get_dependencies(self, service_id: str) -> List[DependencyEdge]:
        """Return all edges where source_id == service_id."""
        ...

    @abstractmethod
    def get_dependents(self, service_id: str) -> List[DependencyEdge]:
        """Return all edges where target_id == service_id."""
        ...

    @abstractmethod
    def get_method(self, method_id: str) -> Optional[MethodNode]:
        """Retrieve a method node by ID."""
        ...

    @abstractmethod
    def get_call_path(
        self, from_method_id: str, to_method_id: str, max_depth: int = 5
    ) -> List[List[str]]:
        """Return all call paths from one method to another (BFS)."""
        ...

    @abstractmethod
    def get_methods_for_service(self, service_id: str) -> List[MethodNode]:
        """Return all method nodes belonging to a service."""
        ...

    @abstractmethod
    def get_callers_of_method(self, method_id: str) -> List[MethodNode]:
        """Return all MethodNodes that call the given method (reverse call lookup).

        Uses ``method_call`` edges in the store where ``target_id == method_id``.
        Works for all languages as long as call edges were built by
        :class:`~corbell.core.graph.method_graph.MethodGraphBuilder`.
        """
        ...

    @abstractmethod
    def get_flows_for_method(self, method_id: str) -> List[Dict[str, Any]]:
        """Return flows that include the given method as a step.

        Returns a list of dicts with keys ``flow_id``, ``flow_name``,
        ``step`` (1-based position in the flow), and ``entry_method_id``.
        """
        ...

    @abstractmethod
    def get_all_nodes_summary(self) -> Dict[str, Any]:
        """Return a summary of all nodes and edges for display."""
        ...

    @abstractmethod
    def to_mermaid(self) -> str:
        """Return a Mermaid representation of the graph."""
        ...

    @abstractmethod
    def to_json(self) -> str:
        """Return the JSON representation of the service graph."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all data from the store."""
        ...
