# cft

`cft` is a CloudFront TUI/CLI project for distribution discovery, usage analysis,
and billing/log correlation.

## Development setup

```bash
uv sync --extra dev
uv run pytest
uv run cft --help
uv run cft dev
```

The project uses a `src/` layout and is packaged via `pyproject.toml`.

Use `uv run cft` and `uv run cft dev` from the repo root when you are not
activating the virtual environment manually.

`uv run cft dev` launches Textual in development mode so CSS changes in
[`src/cft/tui/cft.tcss`](/home/kevin/Documents/cft/src/cft/tui/cft.tcss) are
reflected while the app is running. This uses Textual's built-in CSS file
watching inside the app rather than an external `textual run` launcher.
