# ADR-003: Prefer the Termux system Python runtime on Android

## Status

Accepted

## Date

2026-07-16

## Context

AWS Query-protocol services such as CloudFront return XML. Botocore parses those responses with
Python's XML facilities, which require CPython's `pyexpat` extension. Termux builds that extension
against the native `libexpat` package under `$PREFIX`.

An interpreter selected independently of the Termux package environment can lack that extension
or its linked library. In that case CloudFront loading fails with the misleading low-level message
`No module named expat; use SimpleXMLTreeBuilder instead`, even when `~/.aws/credentials` is valid.
Expat is not an application-level Python dependency and installing a similarly named PyPI package
would not repair the CPython runtime.

## Decision

Configure `uv` to prefer system Python installations. On Termux, setup explicitly creates or
updates the project environment from `$PREFIX/bin/python` and verifies `xml.parsers.expat` both
outside and inside `uv run` before starting cft.

Keep AWS startup failures inside the existing retryable TUI state. Detect missing `pyexpat` or
`libexpat` in the exception chain and present Python/Termux repair commands separately from AWS
credential remediation.

## Alternatives Considered

- Add an `expat` dependency from PyPI: rejected because `pyexpat` is a CPython standard-library C
  extension and the native library is managed by Termux's package manager.
- Replace botocore's XML parser: rejected because XML parsing is internal to botocore and many AWS
  Query-protocol services depend on it. Maintaining a parser fork would be fragile and larger than
  repairing the supported Python runtime.
- Require uv-managed Python on every platform: rejected because Android-linked CPython extensions
  need to match Termux's native libraries.
- Disable managed Python globally with `only-system`: rejected because desktop users may
  legitimately rely on uv to download Python when no compatible system interpreter is installed.

## Consequences

- Fresh project environments prefer an already installed compatible system Python; uv can still
  use a managed interpreter when no compatible system installation exists.
- Existing Termux environments may need one explicit
  `uv sync --python "$PREFIX/bin/python"` invocation.
- Termux installation now explicitly includes `libexpat` alongside `python` and `duckdb`.
- Broken XML support produces a Python runtime recovery message and remains retryable without
  restarting the TUI.
