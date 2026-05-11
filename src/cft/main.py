from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

import typer

from cft.tui.app import run_tui

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="CloudFront TUI and CLI for usage, billing, and log analysis.",
)


@app.callback()
def root(
    ctx: typer.Context,
    profile: Annotated[
        str | None,
        typer.Option("--profile", "-p", help="AWS profile to use."),
    ] = None,
) -> None:
    """CloudFront TUI and CLI for usage, billing, and log analysis."""
    if ctx.invoked_subcommand is None:
        run_tui(profile_name=profile)


def _package_version() -> str:
    try:
        return version("cft")
    except PackageNotFoundError:
        return "0.1.0"


@app.command("version")
def version_command() -> None:
    """Print the installed package version."""
    typer.echo(_package_version())


@app.command("dev")
def dev_command(
    profile: Annotated[
        str | None,
        typer.Option("--profile", "-p", help="AWS profile to use while developing."),
    ] = None,
) -> None:
    """Run the Textual app in dev mode with live CSS editing."""
    run_tui(profile_name=profile, watch_css=True)


def main() -> None:
    app()
