"""Workspace configuration loader for Corbell."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ServiceConfig(BaseModel):
    """A single service definition in workspace.yaml."""

    id: str
    repo: str
    language: str = "python"
    tags: List[str] = Field(default_factory=list)
    resolved_path: Optional[Path] = Field(default=None, exclude=True)

    model_config = {"extra": "ignore"}


class StorageBackendConfig(BaseModel):
    """Storage backend configuration."""

    backend: str = "sqlite"
    path: str = ".corbell/workspace.db"

    model_config = {"extra": "ignore"}


class StorageConfig(BaseModel):
    """Storage sub-config."""

    graph: StorageBackendConfig = Field(default_factory=StorageBackendConfig)
    embeddings: StorageBackendConfig = Field(default_factory=StorageBackendConfig)
    model: str = "all-MiniLM-L6-v2"

    model_config = {"extra": "ignore"}


class ExistingDocsConfig(BaseModel):
    """Configuration for existing design doc scanning."""

    auto_scan: bool = True
    paths: List[str] = Field(default_factory=list)
    patterns: List[str] = Field(
        default_factory=lambda: [
            "*.design.md",
            "*-spec.md",
            "RFC-*.md",
            "ADR-*.md",
            "DESIGN.md",
            "*-design.md",
            "*_design.md",
        ]
    )

    model_config = {"extra": "ignore"}


class SpecConfig(BaseModel):
    """Spec output configuration."""

    output_dir: str = "specs/"
    template: str = "default"

    model_config = {"extra": "ignore"}


class NotionIntegration(BaseModel):
    """Notion integration config."""

    token: Optional[str] = None
    parent_page_id: Optional[str] = None

    model_config = {"extra": "ignore"}


class LinearIntegration(BaseModel):
    """Linear integration config."""

    api_key: Optional[str] = None
    team_id: Optional[str] = None
    default_project_id: Optional[str] = None

    model_config = {"extra": "ignore"}


class IntegrationsConfig(BaseModel):
    """External integrations."""

    notion: NotionIntegration = Field(default_factory=NotionIntegration)
    linear: LinearIntegration = Field(default_factory=LinearIntegration)

    model_config = {"extra": "ignore"}


class LLMConfig(BaseModel):
    """LLM provider configuration.

    Local providers: openai, anthropic, ollama.
    Cloud providers: aws (Bedrock), azure (Azure OpenAI), gcp (Vertex AI).

    API key can be provided here or via env vars:
    ANTHROPIC_API_KEY, OPENAI_API_KEY, AZURE_OPENAI_API_KEY, CORBELL_LLM_API_KEY
    """

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: Optional[str] = None

    # AWS Bedrock
    aws_region: Optional[str] = None

    # Azure OpenAI
    azure_endpoint: Optional[str] = None
    azure_deployment: Optional[str] = None
    azure_api_version: Optional[str] = None

    # GCP Vertex AI
    gcp_project: Optional[str] = None
    gcp_region: Optional[str] = None

    model_config = {"extra": "ignore"}

    def resolved_api_key(self) -> Optional[str]:
        """Return the API key, resolving env var placeholders if needed."""
        key = self.api_key or ""
        if key.startswith("${") and key.endswith("}"):
            var = key[2:-1]
            return os.environ.get(var)
        if key:
            return key
        # Cloud providers use their own credential chains (no API key needed)
        if self.provider in ("aws", "gcp"):
            return None
        # Fall back to well-known env vars
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "azure": "AZURE_OPENAI_API_KEY",
            "ollama": None,
        }
        env_var = env_map.get(self.provider.lower(), "CORBELL_LLM_API_KEY")
        if env_var:
            return os.environ.get(env_var) or os.environ.get("CORBELL_LLM_API_KEY")
        return None


class WorkspaceInfo(BaseModel):
    """Top-level workspace metadata."""

    name: str = "my-platform"
    root: str = ".."

    model_config = {"extra": "ignore"}


class WorkspaceConfig(BaseModel):
    """Root workspace configuration model (parsed from workspace.yaml)."""

    version: str = "1"
    workspace: WorkspaceInfo = Field(default_factory=WorkspaceInfo)
    services: List[ServiceConfig] = Field(default_factory=list)
    existing_docs: ExistingDocsConfig = Field(default_factory=ExistingDocsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    spec: SpecConfig = Field(default_factory=SpecConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # Internal: path this config was loaded from
    _config_path: Optional[Path] = None

    model_config = {"extra": "ignore"}

    def resolve_paths(self, config_dir: Path) -> "WorkspaceConfig":
        """Resolve relative repo paths to absolute paths under config_dir."""
        for svc in self.services:
            raw = svc.repo
            if raw.startswith("${"):
                var = raw[2:-1]
                raw = os.environ.get(var, raw)
            p = Path(raw)
            if not p.is_absolute():
                p = (config_dir / p).resolve()
            svc.resolved_path = p
        return self

    def db_path(self, config_dir: Path) -> Path:
        """Return absolute path to the SQLite DB file."""
        raw = self.storage.graph.path
        p = Path(raw)
        if not p.is_absolute():
            p = (config_dir / p).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def spec_output_dir(self, config_dir: Path) -> Path:
        """Return absolute path to the spec output directory."""
        p = Path(self.spec.output_dir)
        if not p.is_absolute():
            p = (config_dir / p).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} references in dict/list/str values."""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            var = value[2:-1]
            return os.environ.get(var, value)
        return value
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(i) for i in value]
    return value


def load_workspace(path: Path | str) -> "WorkspaceConfig":
    """Load and parse a workspace.yaml file.

    Args:
        path: Path to ``workspace.yaml`` or the directory containing it.

    Returns:
        Parsed and path-resolved :class:`WorkspaceConfig`.

    Raises:
        FileNotFoundError: If the workspace file does not exist.
        ValueError: If the file is not valid YAML or fails schema validation.
    """
    path = Path(path)
    if path.is_dir():
        path = path / "workspace.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Workspace file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    raw = _expand_env(raw)
    config = WorkspaceConfig.model_validate(raw)
    config._config_path = path
    config.resolve_paths(path.parent)
    return config


def find_workspace_root(start: Path | str | None = None) -> Optional[Path]:
    """Walk up directories looking for corbell-data/workspace.yaml.

    Args:
        start: Directory to start searching from (default: cwd).

    Returns:
        Path to the **directory** containing ``corbell-data/workspace.yaml``, or
        ``None`` if not found.
    """
    current = Path(start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        ws = candidate / "corbell-data" / "workspace.yaml"
        if ws.exists():
            return candidate
        ws2 = candidate / "workspace.yaml"
        if ws2.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Auto-detection helpers for `corbell init`
# ---------------------------------------------------------------------------

_EXT_LANG: Dict[str, str] = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".go": "go",
    ".java": "java",
    ".cs": "csharp",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
}

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv",
    "dist", "build", "vendor", "target", ".tox",
}

_MAX_SIBLING_DETECT = 8   # max sibling repos scanned during init
_MAX_SIBLING_DISPLAY = 5  # max sibling repos shown / written to workspace.yaml


def detect_language(repo_path: Path) -> str:
    """Return the dominant source language in a repo by counting file extensions.

    Args:
        repo_path: Root of the repository to scan.

    Returns:
        Language string (e.g. ``"python"``, ``"typescript"``). Falls back to
        ``"python"`` if no recognised source files are found.
    """
    counts: Counter = Counter()
    try:
        for f in repo_path.rglob("*"):
            if not f.is_file():
                continue
            if any(part in _SKIP_DIRS for part in f.parts):
                continue
            lang = _EXT_LANG.get(f.suffix.lower())
            if lang:
                counts[lang] += 1
    except OSError:
        pass
    return counts.most_common(1)[0][0] if counts else "python"


def detect_llm_provider() -> tuple[str, str]:
    """Return ``(provider, model)`` based on env vars present in the environment.

    Checks common API key env vars in priority order. For cloud providers
    (AWS/Azure/GCP) the model string is left empty because model IDs are
    console-specific; the user must fill those in.

    Returns:
        Tuple of ``(provider_name, model_string)``. ``model_string`` is empty
        for cloud providers.
    """
    checks = [
        ("ANTHROPIC_API_KEY", "anthropic", "claude-sonnet-4-6"),
        ("OPENAI_API_KEY", "openai", "gpt-4o"),
        ("BEDROCK_API_KEY", "aws", ""),
        ("AWS_ACCESS_KEY_ID", "aws", ""),
        ("AZURE_OPENAI_API_KEY", "azure", ""),
        ("GOOGLE_APPLICATION_CREDENTIALS", "gcp", ""),
    ]
    for env_var, provider, model in checks:
        if os.environ.get(env_var):
            return provider, model
    return "anthropic", "claude-sonnet-4-6"


def find_sibling_git_repos(target: Path, max_repos: int = _MAX_SIBLING_DETECT) -> List[Path]:
    """Return sibling directories of *target* that contain a ``.git`` folder.

    Args:
        target: The current workspace root (excluded from results).
        max_repos: Maximum number of siblings to return.

    Returns:
        Sorted list of sibling :class:`Path` objects, up to *max_repos*.
    """
    repos: List[Path] = []
    try:
        for child in sorted(target.parent.iterdir()):
            if child == target or not child.is_dir():
                continue
            if (child / ".git").exists():
                repos.append(child)
            if len(repos) >= max_repos:
                break
    except OSError:
        pass
    return repos


class InitDetection(NamedTuple):
    """Results of auto-detection performed during ``corbell init``."""

    workspace_name: str
    current_repo_detected: bool
    current_language: str
    llm_provider: str
    llm_model: str
    llm_env_var_found: bool
    sibling_repos: List[Path]


def detect_init_config(target_dir: Path) -> InitDetection:
    """Auto-detect workspace configuration from the environment.

    Checks whether *target_dir* is a git repo, detects its dominant language,
    finds sibling repos, and sniffs available LLM credentials.

    Args:
        target_dir: Directory where ``corbell init`` is being run.

    Returns:
        :class:`InitDetection` with everything discovered.
    """
    workspace_name = target_dir.name
    is_git_repo = (target_dir / ".git").exists()
    language = detect_language(target_dir) if is_git_repo else "python"
    provider, model = detect_llm_provider()
    env_var_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "aws": "BEDROCK_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
        "gcp": "GOOGLE_APPLICATION_CREDENTIALS",
        "ollama": None,
    }
    env_var = env_var_map.get(provider)
    env_found = bool(env_var and os.environ.get(env_var))
    siblings = find_sibling_git_repos(target_dir)
    return InitDetection(
        workspace_name=workspace_name,
        current_repo_detected=is_git_repo,
        current_language=language,
        llm_provider=provider,
        llm_model=model,
        llm_env_var_found=env_found,
        sibling_repos=siblings,
    )


def init_workspace_yaml(target_dir: Path, detection: Optional[InitDetection] = None) -> Path:
    """Write a workspace.yaml pre-filled with auto-detected configuration.

    Args:
        target_dir: Root directory for the new workspace.
        detection: Pre-computed detection result. If ``None``, detection is
            run automatically.

    Returns:
        Path to the written ``workspace.yaml``.
    """
    if detection is None:
        detection = detect_init_config(target_dir)

    out_dir = target_dir / "corbell-data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "workspace.yaml"

    # ------------------------------------------------------------------ #
    # Services block                                                       #
    # ------------------------------------------------------------------ #
    if detection.current_repo_detected:
        services_block = f"""\
services:
  - id: {detection.workspace_name}
    repo: .
    language: {detection.current_language}
    tags: [core]
"""
    else:
        services_block = """\
services:
  - id: my-service
    repo: ../my-service
    language: python
    tags: [core]
"""

    if detection.sibling_repos:
        services_block += "\n  # Nearby repos detected — uncomment to add:\n"
        for sibling in detection.sibling_repos[:_MAX_SIBLING_DISPLAY]:
            lang = detect_language(sibling)
            services_block += (
                f"  # - id: {sibling.name}\n"
                f"  #   repo: ../{sibling.name}\n"
                f"  #   language: {lang}\n"
            )

    # ------------------------------------------------------------------ #
    # LLM block                                                            #
    # ------------------------------------------------------------------ #
    p = detection.llm_provider
    m = detection.llm_model

    if p == "anthropic":
        llm_block = f"""\
llm:
  provider: anthropic
  model: {m}
  api_key: ${{ANTHROPIC_API_KEY}}
"""
    elif p == "openai":
        llm_block = f"""\
llm:
  provider: openai
  model: {m}
  api_key: ${{OPENAI_API_KEY}}
"""
    elif p == "aws":
        llm_block = """\
llm:
  provider: aws
  model: ""  # paste your Bedrock model ID from the AWS console
  api_key: ${BEDROCK_API_KEY}
  aws_region: us-east-1
"""
    elif p == "azure":
        llm_block = """\
llm:
  provider: azure
  model: gpt-4o
  api_key: ${AZURE_OPENAI_API_KEY}
  azure_endpoint: ${AZURE_OPENAI_ENDPOINT}
  azure_deployment: ${AZURE_OPENAI_DEPLOYMENT}
"""
    elif p == "gcp":
        llm_block = """\
llm:
  provider: gcp
  model: ""  # paste your Vertex model ID from the GCP console
  gcp_project: ${GCP_PROJECT}
  gcp_region: us-central1
"""
    else:
        llm_block = """\
llm:
  provider: anthropic
  model: claude-sonnet-4-6
  api_key: ${ANTHROPIC_API_KEY}
"""

    # ------------------------------------------------------------------ #
    # Full template                                                        #
    # ------------------------------------------------------------------ #
    template = f"""\
version: "1"

workspace:
  name: "{detection.workspace_name}"
  root: ".."

{services_block}
existing_docs:
  auto_scan: true
  paths: []
  patterns:
    - "*.design.md"
    - "*-spec.md"
    - "RFC-*.md"
    - "ADR-*.md"
    - "DESIGN.md"

storage:
  graph:
    backend: sqlite
    path: .corbell/workspace.db
  embeddings:
    backend: sqlite
    path: .corbell/workspace.db
  model: all-MiniLM-L6-v2

spec:
  output_dir: specs/
  template: default

integrations:
  notion:
    token: ${{CORBELL_NOTION_TOKEN}}
    parent_page_id: ${{CORBELL_NOTION_PAGE_ID}}
  linear:
    api_key: ${{CORBELL_LINEAR_API_KEY}}
    team_id: ${{CORBELL_LINEAR_TEAM_ID}}
    default_project_id: ${{CORBELL_LINEAR_PROJECT_ID}}

{llm_block}"""

    out.write_text(template, encoding="utf-8")
    return out
