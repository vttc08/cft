from __future__ import annotations

import ast
import subprocess
import shutil
from pathlib import Path

import pytest

from cft.parquet import (
    DuckDBCliQueryEngine,
    DuckDBPythonQueryEngine,
    FallbackParquetQueryEngine,
    ParquetBackendUnavailableError,
    ParquetQueryError,
    build_parquet_query_engine,
    is_termux,
)
from cft.startup_trace import StartupTrace


def test_application_modules_do_not_import_duckdb_directly() -> None:
    source_root = Path(__file__).parents[1] / "src" / "cft"
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "duckdb" for alias in node.names):
                    violations.append(str(path.relative_to(source_root)))
            elif isinstance(node, ast.ImportFrom) and node.module == "duckdb":
                violations.append(str(path.relative_to(source_root)))

    assert violations == []


def test_termux_detection_uses_android_and_termux_paths(monkeypatch) -> None:
    monkeypatch.setattr("cft.parquet.sys.platform", "android")
    assert is_termux(environ={}) is True

    monkeypatch.setattr("cft.parquet.sys.platform", "linux")
    assert is_termux(environ={"PREFIX": "/data/data/com.termux/files/usr"}) is True
    assert is_termux(environ={"HOME": "/data/user/0/com.termux/files/home"}) is True
    assert is_termux(environ={"HOME": "/home/kevin", "PREFIX": "/usr"}) is False


def test_termux_factory_prefers_cli_without_importing_duckdb(monkeypatch) -> None:
    monkeypatch.setattr("cft.parquet.sys.platform", "android")

    engine = build_parquet_query_engine(environ={})

    assert isinstance(engine, DuckDBCliQueryEngine)


def test_desktop_factory_prefers_python_with_cli_fallback(monkeypatch) -> None:
    monkeypatch.setattr("cft.parquet.sys.platform", "linux")

    engine = build_parquet_query_engine(environ={"HOME": "/home/test"})

    assert isinstance(engine, FallbackParquetQueryEngine)
    assert isinstance(engine.primary, DuckDBPythonQueryEngine)
    assert isinstance(engine.fallback, DuckDBCliQueryEngine)


def test_cli_engine_queries_multiple_paths_as_json_and_escapes_quotes(tmp_path) -> None:
    captured: dict[str, object] = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='[{"distribution_id":"E123","upload_bytes":42}]\n',
            stderr="",
        )

    trace = StartupTrace(enabled=True)
    engine = DuckDBCliQueryEngine(
        executable="/termux/bin/duckdb",
        runner=runner,
        timeout_seconds=17,
        trace=trace,
    )
    files = [tmp_path / "first file.parquet", tmp_path / "second's.parquet"]

    rows = engine.query(files, "SELECT * FROM data")

    assert rows == ({"distribution_id": "E123", "upload_bytes": 42},)
    command = captured["command"]
    assert command[:5] == [
        "/termux/bin/duckdb",
        "-batch",
        "-bail",
        "-json",
        "-c",
    ]
    assert "first file.parquet" in command[5]
    assert "second''s.parquet" in command[5]
    assert "read_parquet([" in command[5]
    assert captured["kwargs"] == {
        "capture_output": True,
        "text": True,
        "timeout": 17,
        "check": False,
    }
    assert trace.events[-1].fields["backend"] == "cli"


def test_cli_engine_reports_missing_executable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("cft.parquet.shutil.which", lambda _: None)
    engine = DuckDBCliQueryEngine()

    with pytest.raises(ParquetBackendUnavailableError, match="pkg install duckdb"):
        engine.query([tmp_path / "data.parquet"], "SELECT * FROM data")


def test_cli_engine_reports_timeout(tmp_path) -> None:
    def runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    engine = DuckDBCliQueryEngine(
        executable="duckdb",
        runner=runner,
        timeout_seconds=3,
    )

    with pytest.raises(ParquetQueryError, match="timed out after 3 seconds"):
        engine.query([tmp_path / "data.parquet"], "SELECT * FROM data")


@pytest.mark.parametrize(
    ("completed", "expected"),
    (
        (
            subprocess.CompletedProcess(
                ["duckdb"],
                1,
                stdout="",
                stderr="Catalog Error: bad column",
            ),
            "exit code 1: Catalog Error",
        ),
        (
            subprocess.CompletedProcess(
                ["duckdb"],
                0,
                stdout="not-json",
                stderr="",
            ),
            "invalid JSON",
        ),
        (
            subprocess.CompletedProcess(
                ["duckdb"],
                0,
                stdout='{"value": 1}',
                stderr="",
            ),
            "unexpected JSON result shape",
        ),
    ),
)
def test_cli_engine_reports_command_and_output_errors(tmp_path, completed, expected) -> None:
    engine = DuckDBCliQueryEngine(
        executable="duckdb",
        runner=lambda *args, **kwargs: completed,
    )

    with pytest.raises(ParquetQueryError, match=expected):
        engine.query([tmp_path / "data.parquet"], "SELECT * FROM data")


def test_cli_engine_does_not_launch_for_empty_file_list() -> None:
    engine = DuckDBCliQueryEngine(
        executable="duckdb",
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("runner must not be called")
        ),
    )

    assert engine.query([], "SELECT * FROM data") == ()


@pytest.mark.skipif(shutil.which("duckdb") is None, reason="DuckDB CLI is not installed")
def test_cli_engine_queries_real_parquet_file(tmp_path) -> None:
    executable = shutil.which("duckdb")
    assert executable is not None
    parquet_path = tmp_path / "sample.parquet"
    subprocess.run(
        [
            executable,
            "-batch",
            "-bail",
            "-c",
            (
                "COPY (SELECT 'E123' AS distribution_id, 42 AS upload_bytes) "
                f"TO '{parquet_path}' (FORMAT PARQUET);"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    rows = DuckDBCliQueryEngine(executable=executable).query(
        [parquet_path],
        "SELECT distribution_id, upload_bytes FROM data",
    )

    assert rows == ({"distribution_id": "E123", "upload_bytes": 42},)


def test_fallback_engine_only_falls_back_when_backend_is_unavailable(tmp_path) -> None:
    class Engine:
        def __init__(self, result=None, error=None) -> None:
            self.result = result
            self.error = error
            self.calls = 0

        def query(self, files, sql):
            self.calls += 1
            if self.error is not None:
                raise self.error
            return self.result

    primary = Engine(error=ParquetBackendUnavailableError("missing"))
    fallback = Engine(result=({"value": 42},))
    engine = FallbackParquetQueryEngine(primary, fallback)

    assert engine.query([tmp_path / "data.parquet"], "SELECT 42") == (
        {"value": 42},
    )
    assert primary.calls == fallback.calls == 1

    primary.error = ParquetQueryError("bad query")
    with pytest.raises(ParquetQueryError, match="bad query"):
        engine.query([tmp_path / "data.parquet"], "SELECT nope")
    assert fallback.calls == 1
