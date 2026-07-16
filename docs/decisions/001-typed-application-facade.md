# ADR-001: Use a typed application facade over profile state

## Status

Accepted

## Date

2026-07-16

## Context

The Textual application previously constructed boto3-backed services, coordinated inventory,
usage, standard-log, and billing refreshes, merged source values, and wrote profile state and
configuration. A future CLI would have needed to duplicate that orchestration or call into UI
code. The profile `state.json` already contains the cached values needed by read-oriented
frontends, but it also contains internal cache metadata such as ETags, source keys, and local
Parquet paths.

## Decision

Frontends depend on a typed `CftApplication` facade. The concrete application service owns
refresh orchestration, configuration commands, and conversion of source results into immutable
dashboard snapshots. Cache-aware AWS and data-export services share a `ProfileStateRepository`
for the profile-scoped `state.json` file.

`state.json` remains canonical internal, versioned storage. It is not the stable JSON contract
for future CLI output. Textual keeps rendering, terminal adaptivity, and interaction logic, but
does not import AWS services or write state/configuration directly. Synchronous boto3 work is
called through Textual workers.

## Alternatives Considered

- Direct JSON parsing in Textual: rejected because it couples presentation code to cache schema
  evolution and would repeat parsing in the CLI.
- A separate `view.json`: rejected because it duplicates data and introduces synchronization and
  partial-update failure modes.
- A passive TUI plus external refresh process: rejected because it adds process lifecycle and
  status coordination without a current requirement.

## Consequences

- TUI and future CLI commands can share the same load, refresh, configuration, and mutation use
  cases.
- Source-specific cache fields and freshness policies remain available without leaking into
  presentation code.
- State writes still use atomic replacement. `ProfileStateRepository.update` is the single seam
  where portable cross-process locking must be added with the first mutating CLI command.
- Future CLI JSON requires a separate stable serializer over application snapshots.
