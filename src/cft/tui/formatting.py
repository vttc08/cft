from __future__ import annotations


def display_data_export_prefix(prefix: str | None) -> str:
    text = str(prefix or "").strip().strip("/")
    return f"/{text}" if text else "/"


def display_cwl_log_group(log_group: str | None) -> str:
    text = str(log_group or "").strip()
    if not text:
        return "Not configured"
    if ":log-group:" in text:
        return text.rsplit(":log-group:", 1)[-1].removesuffix(":*")
    return text.removesuffix(":*")
