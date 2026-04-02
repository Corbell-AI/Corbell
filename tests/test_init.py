"""Tests for corbell init auto-detection helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from corbell.core.workspace import (
    InitDetection,
    _MAX_SIBLING_DETECT,
    _MAX_SIBLING_DISPLAY,
    detect_init_config,
    detect_language,
    detect_llm_provider,
    find_sibling_git_repos,
    init_workspace_yaml,
)


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


def test_detect_language_python(tmp_path):
    (tmp_path / "main.py").write_text("")
    (tmp_path / "utils.py").write_text("")
    assert detect_language(tmp_path) == "python"


def test_detect_language_typescript(tmp_path):
    for name in ("app.ts", "index.tsx", "helper.ts"):
        (tmp_path / name).write_text("")
    (tmp_path / "readme.py").write_text("")  # one python file — ts wins
    assert detect_language(tmp_path) == "typescript"


def test_detect_language_go(tmp_path):
    for name in ("main.go", "server.go", "handler.go"):
        (tmp_path / name).write_text("")
    assert detect_language(tmp_path) == "go"


def test_detect_language_no_sources_falls_back_to_python(tmp_path):
    (tmp_path / "README.md").write_text("")
    (tmp_path / "Makefile").write_text("")
    assert detect_language(tmp_path) == "python"


def test_detect_language_skips_node_modules(tmp_path):
    node = tmp_path / "node_modules"
    node.mkdir()
    for name in ("a.js", "b.js", "c.js"):
        (node / name).write_text("")
    (tmp_path / "app.py").write_text("")
    assert detect_language(tmp_path) == "python"


def test_detect_language_skips_venv(tmp_path):
    venv = tmp_path / "venv"
    venv.mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (venv / name).write_text("")
    (tmp_path / "main.ts").write_text("")
    assert detect_language(tmp_path) == "typescript"


def test_detect_language_handles_oserror(tmp_path, monkeypatch):
    def bad_rglob(_self, *args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "rglob", bad_rglob)
    # Should not raise; falls back to "python"
    assert detect_language(tmp_path) == "python"


# ---------------------------------------------------------------------------
# detect_llm_provider
# ---------------------------------------------------------------------------


def test_detect_llm_provider_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider, model = detect_llm_provider()
    assert provider == "anthropic"
    assert model == "claude-sonnet-4-6"


def test_detect_llm_provider_openai(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    provider, model = detect_llm_provider()
    assert provider == "openai"
    assert model == "gpt-4o"


def test_detect_llm_provider_aws(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    provider, model = detect_llm_provider()
    assert provider == "aws"
    assert model == ""  # cloud providers leave model blank


def test_detect_llm_provider_defaults_to_anthropic(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BEDROCK_API_KEY",
                "AWS_ACCESS_KEY_ID", "AZURE_OPENAI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(var, raising=False)
    provider, model = detect_llm_provider()
    assert provider == "anthropic"
    assert model == "claude-sonnet-4-6"


def test_detect_llm_provider_anthropic_takes_priority(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    provider, _ = detect_llm_provider()
    assert provider == "anthropic"


# ---------------------------------------------------------------------------
# find_sibling_git_repos
# ---------------------------------------------------------------------------


def _make_git_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def test_find_sibling_git_repos_basic(tmp_path):
    target = tmp_path / "my-service"
    target.mkdir()
    _make_git_repo(tmp_path, "service-a")
    _make_git_repo(tmp_path, "service-b")
    (tmp_path / "not-a-repo").mkdir()

    siblings = find_sibling_git_repos(target)
    names = {p.name for p in siblings}
    assert "service-a" in names
    assert "service-b" in names
    assert "not-a-repo" not in names
    assert "my-service" not in names


def test_find_sibling_git_repos_excludes_target(tmp_path):
    target = tmp_path / "my-service"
    target.mkdir()
    (target / ".git").mkdir()  # target itself is a git repo
    siblings = find_sibling_git_repos(target)
    assert target not in siblings


def test_find_sibling_git_repos_respects_max(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    for i in range(12):
        _make_git_repo(tmp_path, f"repo-{i:02d}")

    siblings = find_sibling_git_repos(target, max_repos=3)
    assert len(siblings) <= 3


def test_find_sibling_git_repos_default_max(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    for i in range(_MAX_SIBLING_DETECT + 4):
        _make_git_repo(tmp_path, f"repo-{i:02d}")

    siblings = find_sibling_git_repos(target)
    assert len(siblings) <= _MAX_SIBLING_DETECT


def test_find_sibling_git_repos_handles_oserror(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()

    def bad_iterdir(_self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "iterdir", bad_iterdir)
    assert find_sibling_git_repos(target) == []


# ---------------------------------------------------------------------------
# detect_init_config
# ---------------------------------------------------------------------------


def test_detect_init_config_git_repo(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / "main.py").write_text("")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = detect_init_config(tmp_path)

    assert result.workspace_name == tmp_path.name
    assert result.current_repo_detected is True
    assert result.current_language == "python"
    assert result.llm_provider == "anthropic"
    assert result.llm_env_var_found is False


def test_detect_init_config_not_a_git_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = detect_init_config(tmp_path)

    assert result.current_repo_detected is False
    assert result.current_language == "python"  # default when not a repo


def test_detect_init_config_llm_env_found(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    result = detect_init_config(tmp_path)

    assert result.llm_env_var_found is True
    assert result.llm_provider == "anthropic"


def test_detect_init_config_ollama_env_not_found(tmp_path, monkeypatch):
    """ollama has no env var — env_found should always be False for it."""
    (tmp_path / ".git").mkdir()
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "BEDROCK_API_KEY",
                "AWS_ACCESS_KEY_ID", "AZURE_OPENAI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(var, raising=False)

    # Patch detect_llm_provider to return ollama
    import corbell.core.workspace as ws_mod
    monkeypatch.setattr(ws_mod, "detect_llm_provider", lambda: ("ollama", "llama3"))

    result = detect_init_config(tmp_path)
    assert result.llm_env_var_found is False


def test_detect_init_config_sibling_repos(tmp_path, monkeypatch):
    target = tmp_path / "my-service"
    target.mkdir()
    (target / ".git").mkdir()
    _make_git_repo(tmp_path, "other-service")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = detect_init_config(target)
    sibling_names = [p.name for p in result.sibling_repos]
    assert "other-service" in sibling_names


# ---------------------------------------------------------------------------
# init_workspace_yaml
# ---------------------------------------------------------------------------


def test_init_workspace_yaml_creates_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = init_workspace_yaml(tmp_path)
    assert out.exists()
    assert out.name == "workspace.yaml"


def test_init_workspace_yaml_valid_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = init_workspace_yaml(tmp_path)
    parsed = yaml.safe_load(out.read_text())
    assert parsed["version"] == "1"
    assert "services" in parsed
    assert "llm" in parsed


def test_init_workspace_yaml_git_repo_detected(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / "main.py").write_text("")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    out = init_workspace_yaml(tmp_path)
    content = out.read_text()
    assert f"id: {tmp_path.name}" in content
    assert "language: python" in content


def test_init_workspace_yaml_no_git_repo_placeholder(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = init_workspace_yaml(tmp_path)
    content = out.read_text()
    assert "id: my-service" in content


def test_init_workspace_yaml_anthropic_llm_block(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for var in ("OPENAI_API_KEY",):
        monkeypatch.delenv(var, raising=False)

    out = init_workspace_yaml(tmp_path)
    content = out.read_text()
    assert "provider: anthropic" in content
    assert "${ANTHROPIC_API_KEY}" in content


def test_init_workspace_yaml_openai_llm_block(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")

    out = init_workspace_yaml(tmp_path)
    content = out.read_text()
    assert "provider: openai" in content
    assert "${OPENAI_API_KEY}" in content


def test_init_workspace_yaml_sibling_repos_commented(tmp_path, monkeypatch):
    target = tmp_path / "my-service"
    target.mkdir()
    (target / ".git").mkdir()
    _make_git_repo(tmp_path, "other-service")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = init_workspace_yaml(target)
    content = out.read_text()
    assert "# - id: other-service" in content


def test_init_workspace_yaml_sibling_display_cap(tmp_path, monkeypatch):
    """Sibling repos beyond _MAX_SIBLING_DISPLAY are not written to yaml."""
    target = tmp_path / "my-service"
    target.mkdir()
    (target / ".git").mkdir()
    for i in range(_MAX_SIBLING_DISPLAY + 3):
        _make_git_repo(tmp_path, f"sibling-{i:02d}")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = init_workspace_yaml(target)
    content = out.read_text()

    # Count commented-out sibling entries
    sibling_lines = [l for l in content.splitlines() if "# - id: sibling-" in l]
    assert len(sibling_lines) <= _MAX_SIBLING_DISPLAY


def test_init_workspace_yaml_accepts_precomputed_detection(tmp_path):
    detection = InitDetection(
        workspace_name="pre-detected",
        current_repo_detected=True,
        current_language="go",
        llm_provider="openai",
        llm_model="gpt-4o",
        llm_env_var_found=True,
        sibling_repos=[],
    )
    out = init_workspace_yaml(tmp_path, detection)
    content = out.read_text()
    assert "id: pre-detected" in content
    assert "language: go" in content
    assert "provider: openai" in content
