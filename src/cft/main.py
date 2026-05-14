from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated

import typer

from cft.config.paths import get_app_paths
from cft.tui.app import run_tui

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="CloudFront TUI and CLI for usage, billing, and log analysis.",
)
config_app = typer.Typer(help="Inspect and initialize local cft configuration.")
app.add_typer(config_app, name="config")


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


@config_app.command("paths")
def config_paths_command(
    profile: Annotated[
        str | None,
        typer.Option("--profile", "-p", help="Profile name for profile-scoped paths."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print path information as JSON."),
    ] = False,
) -> None:
    """Print the resolved local config, cache, and data paths."""
    paths = get_app_paths()
    profile_name = profile or "default"
    payload = {
        "profile": profile_name,
        "app_home": str(paths.root_dir) if paths.root_dir else "",
        "config_dir": str(paths.config_dir),
        "cache_dir": str(paths.cache_dir),
        "data_dir": str(paths.data_dir),
        "config_file": str(paths.config_file),
        "profile_config_file": str(paths.profile_config_file(profile_name)),
        "profile_state_file": str(paths.profile_state_file(profile_name)),
        "parquet_dir": str(paths.parquet_dir(profile_name)),
    }

    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    for key, value in payload.items():
        typer.echo(f"{key}: {value}")


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
