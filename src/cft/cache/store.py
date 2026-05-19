from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class JsonFileStore:
    path: Path

    @staticmethod
    def _serialize(payload: dict[str, Any]) -> str:
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(self._serialize(payload), encoding="utf-8")
        temporary.replace(self.path)

    def write_if_changed(self, payload: dict[str, Any]) -> bool:
        serialized = self._serialize(payload)
        if self.path.exists():
            try:
                if self.path.read_text(encoding="utf-8") == serialized:
                    return False
            except OSError:
                pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(self.path)
        return True
