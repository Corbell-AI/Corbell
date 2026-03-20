"""Corbell CLI — main Typer app with all command groups."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="corbell",
    help=(
        "🏗️  Corbell — Multi-repo architecture graph, spec generation & review for backend teams.\n\n"
        "Quick start:\n\n"
        "  corbell init            Create workspace.yaml\n\n"
        "  corbell graph build     Scan repos, build service graph\n\n"
        "  corbell spec new        Generate a technical design doc\n\n"
        "  corbell spec review     Review a spec against the graph\n\n"
        "  corbell spec approve    Mark spec approved\n\n"
        "  corbell spec decompose  Break spec into parallel tasks"
    ),
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
console = Console()


# ---------------------------------------------------------------------------
# Sub-apps (command groups registered under the main app)
# ---------------------------------------------------------------------------

from corbell.cli.commands.graph import app as graph_app
from corbell.cli.commands.embeddings import app as embeddings_app
from corbell.cli.commands.docs import app as docs_app
from corbell.cli.commands.spec import app as spec_app
from corbell.cli.commands.export import app as export_app
from corbell.cli.commands.mcp import app as mcp_app
from corbell.cli.commands.ui import app as ui_app



# Top-level `corbell init` shortcut
@app.command("init")
def init(
    directory: str = typer.Option(None, "--dir", "-d", help="Target directory (default: cwd)."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing workspace.yaml."),
):
    """Initialize a Corbell workspace (creates corbell-data/workspace.yaml)."""
    from pathlib import Path
    from corbell.core.workspace import init_workspace_yaml

    target = (Path(directory) if directory else Path.cwd()).resolve()
    ws_file = target / "corbell" / "workspace.yaml"

    if ws_file.exists() and not force:
        console.print(
            f"[yellow]⚠️  workspace.yaml already exists at {ws_file}[/yellow]\n"
            "Use --force to overwrite."
        )
        raise typer.Exit(0)

    out = init_workspace_yaml(target)
    console.print(f"[green]✓[/green] Created [bold]{out}[/bold]")
    console.print("\nNext steps:")
    console.print("  1. Edit [bold]corbell-data/workspace.yaml[/bold] — add your repo paths")
    console.print("  2. Set [bold]ANTHROPIC_API_KEY[/bold] or [bold]OPENAI_API_KEY[/bold]")
    console.print("  3. [bold]corbell graph build[/bold]")
    console.print("  4. [bold]corbell embeddings build[/bold]")
    console.print("  5. [bold]corbell spec new[/bold]")


app.add_typer(graph_app, name="graph", help="Service dependency graph commands.")
app.add_typer(embeddings_app, name="embeddings", help="Code embedding index commands.")
app.add_typer(docs_app, name="docs", help="Design doc scan and pattern learning.")
app.add_typer(spec_app, name="spec", help="Spec lifecycle: new → lint → review → approve → decompose.")
app.add_typer(export_app, name="export", help="Export to Notion or Linear.")
app.add_typer(mcp_app, name="mcp", help="Model Context Protocol (MCP) server integration.")
app.add_typer(ui_app, name="ui", help="Architecture graph browser UI — start with: corbell ui serve")


def main():
    app()


if __name__ == "__main__":
    main()
