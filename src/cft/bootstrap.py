from __future__ import annotations

from cft.application.service import CftApplicationService
from cft.config.paths import AppPaths
from cft.startup_trace import StartupTrace


def build_application(
    profile_name: str | None = None,
    *,
    paths: AppPaths | None = None,
    startup_trace: StartupTrace | None = None,
) -> CftApplicationService:
    return CftApplicationService(
        profile_name=profile_name,
        paths=paths,
        startup_trace=startup_trace,
    )


def run_tui(profile_name: str | None = None, *, watch_css: bool = False) -> None:
    from cft.tui.app import CftApp

    CftApp(application=build_application(profile_name), watch_css=watch_css).run()


def profile_startup(profile_name: str | None = None, *, refresh: bool = False) -> str:
    trace = StartupTrace(enabled=True)
    application = build_application(profile_name, startup_trace=trace)
    with trace.step("startup.total", refresh=refresh):
        application.load_dashboard(refresh=refresh)
    return trace.render_text()
