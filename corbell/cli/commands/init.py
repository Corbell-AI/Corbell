"""corbell init — create a pre-filled workspace.yaml from auto-detection."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(help="Initialize a Corbell workspace.")
console = Console()


def init_cmd(
    directory: Optional[Path] = typer.Option(
        None, "--dir", "-d", help="Target directory (default: current directory)."
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing workspace.yaml."),
):
    """Initialize a Corbell workspace.

    Auto-detects the current git repo, dominant language, nearby sibling repos,
    and any LLM API key already set in the environment. The generated
    workspace.yaml is pre-filled so you can run ``corbell graph build``
    immediately without manual editing in most cases.
    """
    from corbell.core.workspace import _MAX_SIBLING_DISPLAY, detect_init_config, init_workspace_yaml

    target = (directory or Path.cwd()).resolve()
    ws_file = target / "corbell-data" / "workspace.yaml"

    if ws_file.exists() and not force:
        console.print(
            f"[yellow]⚠️  workspace.yaml already exists at {ws_file}[/yellow]\n"
            "Use --force to overwrite."
        )
        raise typer.Exit(0)

    detection = detect_init_config(target)
    out = init_workspace_yaml(target, detection)

    console.print(f"[green]✓[/green] Created [bold]{out}[/bold]\n")

    # ------------------------------------------------------------------ #
    # Show what was auto-detected                                          #
    # ------------------------------------------------------------------ #
    console.print("[bold]Auto-detected:[/bold]")

    console.print(f"  [green]✓[/green] Workspace name: [cyan]{detection.workspace_name}[/cyan]")

    if detection.current_repo_detected:
        console.print(
            f"  [green]✓[/green] Repo: [cyan]{detection.workspace_name}[/cyan] "
            f"([cyan]{detection.current_language}[/cyan]) — added as service"
        )
    else:
        console.print(
            "  [yellow]◦[/yellow] Not a git repo — placeholder service added, "
            "edit [bold]corbell-data/workspace.yaml[/bold] to set your repo paths"
        )

    if detection.llm_env_var_found:
        console.print(
            f"  [green]✓[/green] LLM: [cyan]{detection.llm_provider}[/cyan] "
            f"(API key found in environment) — model: [cyan]{detection.llm_model or 'set in workspace.yaml'}[/cyan]"
        )
    else:
        console.print(
            f"  [yellow]◦[/yellow] LLM: defaulted to [cyan]{detection.llm_provider}[/cyan] "
            f"— set [bold]ANTHROPIC_API_KEY[/bold] or [bold]OPENAI_API_KEY[/bold] to enable spec generation"
        )

    if detection.sibling_repos:
        names = ", ".join(r.name for r in detection.sibling_repos[:_MAX_SIBLING_DISPLAY])
        extra = (
            f" (+{len(detection.sibling_repos) - _MAX_SIBLING_DISPLAY} more)"
            if len(detection.sibling_repos) > _MAX_SIBLING_DISPLAY
            else ""
        )
        console.print(
            f"  [green]✓[/green] Nearby repos: [cyan]{names}{extra}[/cyan] — "
            "uncomment in workspace.yaml to add them"
        )

    # ------------------------------------------------------------------ #
    # Next steps — skip steps the auto-detection already handled          #
    # ------------------------------------------------------------------ #
    console.print()
    console.print("[bold]Next steps:[/bold]")

    next_steps = []
    if not detection.current_repo_detected or detection.sibling_repos:
        next_steps.append(
            "Review [bold]corbell-data/workspace.yaml[/bold] — add any additional services"
        )
    if not detection.llm_env_var_found:
        next_steps.append(
            "Set your LLM key:  [bold]export ANTHROPIC_API_KEY=sk-ant-...[/bold]  "
            "(or OPENAI_API_KEY)"
        )
    next_steps.append("[bold]corbell graph build[/bold]")
    next_steps.append("[bold]corbell embeddings build[/bold]")
    next_steps.append('[bold]corbell spec new --feature "your feature"[/bold]')

    for i, step_text in enumerate(next_steps, 1):
        console.print(f"  {i}. {step_text}")
