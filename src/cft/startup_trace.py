from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter


@dataclass(frozen=True)
class StartupTraceEvent:
    name: str
    elapsed_ms: float | None = None
    fields: dict[str, object] = field(default_factory=dict)


class StartupTrace:
    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled
        self.events: list[StartupTraceEvent] = []

    @classmethod
    def from_env(cls) -> StartupTrace:
        value = os.environ.get("CFT_STARTUP_TRACE", "")
        enabled = value.strip().lower() not in {"", "0", "false", "no", "off"}
        return cls(enabled=enabled)

    @contextmanager
    def step(self, name: str, **fields: object) -> Iterator[dict[str, object]]:
        if not self.enabled:
            yield {}
            return

        extra: dict[str, object] = {}
        started = perf_counter()
        try:
            yield extra
        except Exception as error:
            self.events.append(
                StartupTraceEvent(
                    name=name,
                    elapsed_ms=(perf_counter() - started) * 1000,
                    fields={**fields, **extra, "status": "error", "error": type(error).__name__},
                )
            )
            raise

        self.events.append(
            StartupTraceEvent(
                name=name,
                elapsed_ms=(perf_counter() - started) * 1000,
                fields={**fields, **extra},
            )
        )

    def emit(self, name: str, **fields: object) -> None:
        if not self.enabled:
            return
        self.events.append(StartupTraceEvent(name=name, fields=dict(fields)))

    def render_text(self) -> str:
        lines = ["cft startup trace"]
        for event in self.events:
            prefix = event.name
            if event.elapsed_ms is not None:
                prefix = f"{prefix}: {event.elapsed_ms:.1f}ms"
            if event.fields:
                details = ", ".join(
                    f"{key}={value}" for key, value in sorted(event.fields.items())
                )
                lines.append(f"- {prefix} [{details}]")
            else:
                lines.append(f"- {prefix}")
        return "\n".join(lines)

    def write(self) -> None:
        if not self.enabled:
            return
        print(self.render_text(), file=sys.stderr)
