from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from cft.startup_trace import StartupTrace

DUCKDB_CLI_TIMEOUT_SECONDS = 300


class ParquetQueryError(RuntimeError):
    """Raised when a local Parquet query cannot be completed."""


class ParquetBackendUnavailableError(ParquetQueryError):
    """Raised when no usable DuckDB backend is available."""


class ParquetQueryEngine(Protocol):
    def query(
        self,
        files: Sequence[Path],
        sql: str,
    ) -> tuple[Mapping[str, object], ...]: ...


def is_termux(*, environ: Mapping[str, str] | None = None) -> bool:
    if sys.platform == "android":
        return True

    environment = os.environ if environ is None else environ
    prefixes = (
        environment.get("TERMUX__PREFIX", ""),
        environment.get("PREFIX", ""),
    )
    homes = (
        environment.get("TERMUX__HOME", ""),
        environment.get("HOME", ""),
    )
    return any(_has_path_suffix(value, ("files", "usr")) for value in prefixes) or any(
        _has_path_suffix(value, ("files", "home")) for value in homes
    )


def _has_path_suffix(value: str, suffix: tuple[str, ...]) -> bool:
    if not value:
        return False
    return Path(value).parts[-len(suffix) :] == suffix


class DuckDBPythonQueryEngine:
    def __init__(self, *, trace: StartupTrace | None = None) -> None:
        self.trace = trace

    def query(
        self,
        files: Sequence[Path],
        sql: str,
    ) -> tuple[Mapping[str, object], ...]:
        if not files:
            return ()

        try:
            duckdb = importlib.import_module("duckdb")
        except (ImportError, OSError) as error:
            raise ParquetBackendUnavailableError(
                "The DuckDB Python module is unavailable."
            ) from error

        if self.trace is not None:
            self.trace.emit("parquet.backend", backend="python")

        connection = None
        try:
            connection = duckdb.connect(database=":memory:")
            connection.read_parquet([str(path) for path in files]).create_view("data")
            result = connection.execute(sql)
            columns = tuple(description[0] for description in result.description or ())
            return tuple(dict(zip(columns, row, strict=True)) for row in result.fetchall())
        except ParquetQueryError:
            raise
        except Exception as error:
            raise ParquetQueryError(f"DuckDB Python query failed: {error}") from error
        finally:
            if connection is not None:
                connection.close()


SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]


class DuckDBCliQueryEngine:
    def __init__(
        self,
        *,
        executable: str | None = None,
        runner: SubprocessRunner = subprocess.run,
        timeout_seconds: int = DUCKDB_CLI_TIMEOUT_SECONDS,
        trace: StartupTrace | None = None,
    ) -> None:
        self.executable = executable
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.trace = trace

    def query(
        self,
        files: Sequence[Path],
        sql: str,
    ) -> tuple[Mapping[str, object], ...]:
        if not files:
            return ()

        executable = self.executable or shutil.which("duckdb")
        if executable is None:
            raise ParquetBackendUnavailableError(
                "DuckDB CLI is required for Parquet analysis on Termux; "
                "run 'pkg install duckdb' and retry."
            )

        if self.trace is not None:
            self.trace.emit("parquet.backend", backend="cli", executable=executable)

        script = f"{_create_data_view_sql(files)}\n{sql.strip().rstrip(';')};"
        command = [
            executable,
            "-batch",
            "-bail",
            "-json",
            "-c",
            script,
        ]
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise ParquetBackendUnavailableError(
                "DuckDB CLI is required for Parquet analysis on Termux; "
                "run 'pkg install duckdb' and retry."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise ParquetQueryError(
                f"DuckDB CLI query timed out after {self.timeout_seconds} seconds."
            ) from error
        except OSError as error:
            raise ParquetBackendUnavailableError(
                f"DuckDB CLI could not be started: {error}"
            ) from error

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            if len(detail) > 500:
                detail = f"{detail[:497]}..."
            raise ParquetQueryError(
                f"DuckDB CLI query failed with exit code {completed.returncode}: {detail}"
            )

        output = completed.stdout.strip()
        if not output:
            return ()
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise ParquetQueryError("DuckDB CLI returned invalid JSON output.") from error
        if not isinstance(payload, list) or not all(
            isinstance(row, dict) for row in payload
        ):
            raise ParquetQueryError("DuckDB CLI returned an unexpected JSON result shape.")
        return tuple(payload)


class FallbackParquetQueryEngine:
    def __init__(
        self,
        primary: ParquetQueryEngine,
        fallback: ParquetQueryEngine,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def query(
        self,
        files: Sequence[Path],
        sql: str,
    ) -> tuple[Mapping[str, object], ...]:
        try:
            return self.primary.query(files, sql)
        except ParquetBackendUnavailableError:
            return self.fallback.query(files, sql)


def build_parquet_query_engine(
    *,
    trace: StartupTrace | None = None,
    environ: Mapping[str, str] | None = None,
) -> ParquetQueryEngine:
    cli = DuckDBCliQueryEngine(trace=trace)
    if is_termux(environ=environ):
        return cli
    return FallbackParquetQueryEngine(
        DuckDBPythonQueryEngine(trace=trace),
        cli,
    )


def _create_data_view_sql(files: Sequence[Path]) -> str:
    paths = ", ".join(_sql_string_literal(str(path)) for path in files)
    return (
        "CREATE OR REPLACE TEMP VIEW data AS "
        f"SELECT * FROM read_parquet([{paths}]);"
    )


def _sql_string_literal(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"
