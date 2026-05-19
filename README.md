# cft

`cft` is a CloudFront TUI/CLI project for distribution discovery, usage analysis,
and billing/log correlation.

## Development setup

```bash
uv sync --extra dev
uv run pytest
uv run cft --help
uv run cft dev
uv run cft startup-profile --profile default
```

The project uses a `src/` layout and is packaged via `pyproject.toml`.

Use `uv run cft` and `uv run cft dev` from the repo root when you are not
activating the virtual environment manually.

Use `uv run cft startup-profile` to print per-step startup timings without
launching the interactive TUI. Set `CFT_STARTUP_TRACE=1` before `uv run cft`
to emit the same trace during a normal TUI startup.

`uv run cft dev` launches Textual in development mode so CSS changes in
[`src/cft/tui/cft.tcss`](/home/kevin/Documents/cft/src/cft/tui/cft.tcss) are
reflected while the app is running. This uses Textual's built-in CSS file
watching inside the app rather than an external `textual run` launcher.

## Local configuration and cache layout

`cft` now keeps all app data under a single home tree by default:

- Linux/macOS/Termux: `~/.cft`
- Windows PowerShell: `C:\Users\<you>\.cft`
- Use `CFT_HOME` to move that tree somewhere else.
- Advanced users can override `CFT_CONFIG_DIR`, `CFT_CACHE_DIR`, and
  `CFT_DATA_DIR` independently if they want an XDG-style split layout.

Inspect the resolved paths with:

```bash
uv run cft config paths --profile default
uv run cft config paths --profile default --json
```

With `CFT_HOME=~/.cft`, the layout is:

```text
~/.cft/
  config/
    config.toml
    default.toml
  cache/
    default/
      state.json
  data/
    data_exports/
      default/
        parquet/
```

AWS credentials stay in `~/.aws/config` and `~/.aws/credentials`; `cft` does not copy access keys into its own config directory.

The distribution browser reads `cache/<profile>/state.json` first. If the inventory cache is fresh, no CloudFront or STS calls are made. If it is stale, the app refreshes from AWS and rewrites the inventory section inside the JSON state file with distribution IDs as keys. The TUI also reads per-distribution CloudWatch usage from each distribution's `cw` object in the same file. When the current-month usage cache is stale, `cft` refreshes `BytesDownloaded` and `Requests` from CloudWatch in `us-east-1`, updates `cw.last_updated` and `cw.month_key`, and falls back to cached values if CloudWatch is unavailable. `cw.upload` remains empty because CloudFront `BytesUploaded` is not treated as reliable for the WebSocket case.

For standard logging uploads, `cft` reads CloudWatch Logs deliveries from the cached CloudFront inventory and batches Logs Insights queries by unique log group. If you standardize all CloudFront standard logs into one CloudWatch Logs group, set `aws.cwl_log_group` in `~/.cft/config/config.toml` or the profile config to force a single shared query path and avoid per-distribution discovery work. The TUI exposes this through the `CWL Logs` setup card, which can list discovered log groups and save a profile-scoped override.
