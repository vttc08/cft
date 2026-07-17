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

### Termux / Android

`cft` supports the current Termux Python packages (Python 3.13 or newer). Update an older
Termux installation and install the native DuckDB CLI before starting the app:

```bash
pkg upgrade
pkg install python duckdb
python -c "import sys; print(sys.version); print(sys.platform)"
duckdb -version
uv run cft
```

On Android, `uv` does not install or build the DuckDB Python module. `cft` calls the Termux
`duckdb` executable for local CUR and CloudFront S3-log Parquet aggregates instead. On Linux,
macOS, and Windows, the installed DuckDB Python module remains the preferred backend.

If the Termux CLI is missing or cannot execute a query, the TUI still loads inventory and
CloudWatch data and shows an actionable warning for the unavailable Parquet-backed feature.
Keep the default `~/.cft` data location where possible; Android shared storage can have scoped
storage, permission, and performance limitations.

## Architecture

`cft` separates frontend rendering from AWS and persistence work:

- `cft.application` exposes typed dashboard, refresh, discovery, and configuration use cases.
- `cft.bootstrap` constructs boto3-backed adapters and launches the selected frontend.
- AWS, CloudWatch Logs, S3 logs, and Data Export services share the profile-scoped state
  repository and retain their existing cache policies.
- CUR and S3-log services share a portable Parquet query interface backed by the DuckDB Python
  module on desktop systems and the DuckDB CLI on Termux.
- `cft.tui` consumes immutable application snapshots and owns only Textual interaction,
  formatting, and responsive layout behavior. Blocking discovery and refresh work runs in
  Textual workers.

The profile `state.json` file is canonical internal storage, not a public JSON API. Future CLI
JSON output should use a separately documented serializer over the same application snapshots.
See [ADR-001](docs/decisions/001-typed-application-facade.md) for the decision and trade-offs.
See [ADR-002](docs/decisions/002-portable-duckdb-backends.md) for the portable DuckDB backend
decision.

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

If credentials are missing, incomplete, malformed, expired, or rejected, the TUI stays open and displays profile-specific recovery guidance. Fix the shared AWS files, environment variables, role, or SSO session, then press `r` or select **Retry** without restarting `cft`.

The distribution browser reads `cache/<profile>/state.json` first. If the inventory cache is fresh, no CloudFront or STS calls are made. If it is stale, the app refreshes from AWS and rewrites the inventory section inside the JSON state file with distribution IDs as keys. The TUI also reads per-distribution CloudWatch usage from each distribution's `cw` object in the same file. When the current-month usage cache is stale, `cft` refreshes `BytesDownloaded` and `Requests` from CloudWatch in `us-east-1`, updates `cw.last_updated` and `cw.month_key`, and falls back to cached values if CloudWatch is unavailable. `cw.upload` remains empty because CloudFront `BytesUploaded` is not treated as reliable for the WebSocket case.

For standard logging uploads, `cft` reads CloudWatch Logs deliveries from the cached CloudFront inventory and batches Logs Insights queries by unique log group. If you standardize all CloudFront standard logs into one CloudWatch Logs group, set `aws.cwl_log_group` in `~/.cft/config/config.toml` or the profile config to force a single shared query path and avoid per-distribution discovery work. The TUI exposes this through the `CWL Logs` setup card, which can list discovered log groups and save a profile-scoped override.
