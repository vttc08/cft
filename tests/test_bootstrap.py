from __future__ import annotations

from cft import bootstrap
from cft.tui import app as tui_app


def test_run_tui_launches_without_credential_preflight(monkeypatch) -> None:
    application = object()
    launched: dict[str, object] = {}

    class FakeCftApp:
        def __init__(self, *, application: object, watch_css: bool) -> None:
            launched["application"] = application
            launched["watch_css"] = watch_css

        def run(self) -> None:
            launched["ran"] = True

    monkeypatch.setattr(bootstrap, "build_application", lambda profile_name: application)
    monkeypatch.setattr(tui_app, "CftApp", FakeCftApp)

    bootstrap.run_tui(profile_name="missing")

    assert launched == {
        "application": application,
        "watch_css": False,
        "ran": True,
    }
