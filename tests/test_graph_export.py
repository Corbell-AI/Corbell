"""Tests for graph export functionality and CLI command."""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from corbell.core.graph.schema import (
    DataStoreNode,
    DependencyEdge,
    QueueNode,
    ServiceNode,
)
from corbell.core.graph.sqlite_store import SQLiteGraphStore
from corbell.cli.commands.graph import app


@pytest.fixture
def store(tmp_db):
    return SQLiteGraphStore(tmp_db)


@pytest.fixture
def populated_store(store):
    # Services
    store.upsert_node(ServiceNode(id="svc-a", name="Service A", repo="/r/a", language="python"))
    store.upsert_node(ServiceNode(id="svc-b", name="Service B", repo="/r/b", language="typescript"))
    
    # Datastore & Queue
    store.upsert_node(DataStoreNode(id="ds:db", kind="postgres", name="Main DB"))
    store.upsert_node(QueueNode(id="q:queue", kind="sqs", name="Main Queue"))
    
    # Edges
    store.upsert_edge(DependencyEdge(source_id="svc-a", target_id="svc-b", kind="http_call"))
    store.upsert_edge(DependencyEdge(source_id="svc-a", target_id="ds:db", kind="db_read"))
    store.upsert_edge(DependencyEdge(source_id="svc-b", target_id="q:queue", kind="queue_publish"))
    
    return store


def test_to_mermaid(populated_store):
    mermaid_str = populated_store.to_mermaid()
    
    # Check node definitions
    assert "svc_a[\"Service A\"]" in mermaid_str
    assert "svc_b[\"Service B\"]" in mermaid_str
    assert "ds_db[(\"Main DB\")]" in mermaid_str
    assert "q_queue>\"Main Queue\"]" in mermaid_str
    
    # Check edge connections (using safe replaced IDs)
    assert "svc_a -- HTTP --> svc_b" in mermaid_str
    assert "svc_a -- Reads --> ds_db" in mermaid_str
    assert "svc_b -- Publishes --> q_queue" in mermaid_str
    
    # Check styling classes
    assert "classDef service fill:#161b22,stroke:#39d353,stroke-width:2px,color:#c9d1d9;" in mermaid_str
    assert "class svc_a service" in mermaid_str
    assert "class ds_db datastore" in mermaid_str
    assert "class q_queue queue" in mermaid_str


def test_to_json(populated_store):
    json_str = populated_store.to_json()
    data = json.loads(json_str)
    
    assert "nodes" in data
    assert "edges" in data
    
    nodes = {n["id"]: n for n in data["nodes"]}
    assert "svc-a" in nodes
    assert nodes["svc-a"]["label"] == "Service A"
    assert nodes["svc-a"]["language"] == "python"
    
    assert "ds:db" in nodes
    assert nodes["ds:db"]["kind"] == "postgres"
    
    assert "q:queue" in nodes
    assert nodes["q:queue"]["kind"] == "sqs"
    
    edges = {(e["source"], e["target"]): e for e in data["edges"]}
    assert ("svc-a", "svc-b") in edges
    assert edges[("svc-a", "svc-b")]["kind"] == "http_call"


def test_cli_export_stdout(populated_store, sample_workspace_yaml, monkeypatch):
    runner = CliRunner()
    
    # Mock workspace config loading to point to our temp db
    def mock_load(ws_dir):
        class MockConfig:
            services = []
            def db_path(self, cfg_dir):
                return populated_store.db_path
        return MockConfig(), Path(sample_workspace_yaml).parent

    monkeypatch.setattr("corbell.cli.commands.graph._load", mock_load)

    # Test mermaid format to stdout
    result = runner.invoke(app, ["export", "--format", "mermaid"])
    assert result.exit_code == 0
    assert "graph LR" in result.stdout
    assert "svc_a -- HTTP --> svc_b" in result.stdout
    
    # Test json format to stdout
    result = runner.invoke(app, ["export", "--format", "json"])
    assert result.exit_code == 0
    assert '"nodes": [' in result.stdout


def test_cli_export_file(populated_store, sample_workspace_yaml, monkeypatch, tmp_path):
    runner = CliRunner()
    
    def mock_load(ws_dir):
        class MockConfig:
            services = []
            def db_path(self, cfg_dir):
                return populated_store.db_path
        return MockConfig(), Path(sample_workspace_yaml).parent

    monkeypatch.setattr("corbell.cli.commands.graph._load", mock_load)

    output_file = tmp_path / "graph.mmd"
    result = runner.invoke(app, ["export", "--format", "mermaid", "--output", str(output_file)])
    assert result.exit_code == 0
    assert output_file.exists()
    
    file_content = output_file.read_text()
    assert "graph LR" in file_content
    assert "svc_a -- HTTP --> svc_b" in file_content
