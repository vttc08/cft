# ADR-002: Use portable DuckDB backends for local Parquet queries

## Status

Accepted

## Date

2026-07-16

## Context

`cft` uses DuckDB for small aggregate queries over downloaded CUR and CloudFront standard-log
Parquet files. The DuckDB Python package publishes desktop wheels but not Android wheels. On
Termux, `uv run cft` therefore attempted a large source build before the application could start,
even though Termux provides a native DuckDB CLI with the Parquet and JSON extensions enabled.

The services also imported `duckdb` at module load time, so skipping the package alone would have
made all application entry points fail during import.

## Decision

All local Parquet reads use a shared typed query interface that returns rows by column name.
Linux, macOS, and Windows prefer an in-process DuckDB Python adapter. Termux uses a subprocess
adapter that invokes `duckdb` without a shell, creates a temporary view over the requested local
files, executes the existing aggregate SQL, and parses JSON output.

The Python dependency is conditional on `sys_platform != 'android'`. Runtime Termux detection
also recognizes the documented Termux prefix and home paths for defensive compatibility. Backend
availability is checked lazily when a Parquet query is needed. Failures in optional Parquet-backed
sources produce dashboard warnings instead of preventing inventory and CloudWatch data from
loading.

## Alternatives Considered

- Build the DuckDB Python package on Android: rejected because it makes installation slow and
  unreliable on resource-constrained phones.
- Use the DuckDB CLI on every platform: rejected because the Python module is faster to start,
  preserves native values, and is already packaged for supported desktop systems.
- Replace DuckDB with PyArrow or another Parquet engine: rejected because it adds another large
  compiled dependency and would duplicate the existing SQL aggregates.
- Make DuckDB an optional desktop extra: rejected because it would change the normal desktop
  installation contract. Current Termux Python reports Android through standard package markers.

## Consequences

- Termux requires current Python packages and `pkg install duckdb`, but `uv` no longer compiles
  DuckDB or the unused Pydantic Core dependency.
- SQL must remain valid in both DuckDB clients, and result conversion must accept Python native
  values and CLI JSON strings/numbers.
- CLI execution has a finite timeout and reports missing executables and query failures with
  actionable messages.
- New Parquet consumers must use the shared adapter rather than importing DuckDB directly.
