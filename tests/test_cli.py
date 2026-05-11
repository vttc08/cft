from typer.testing import CliRunner

from cft import main as main_module
from cft.main import app


runner = CliRunner()


def test_help_renders() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "version" in result.stdout
    assert "CloudFront TUI and CLI" in result.stdout


def test_version_command_renders() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip()


def test_no_args_launches_tui(monkeypatch) -> None:
    launched = {}

    def fake_run_tui(profile_name: str | None = None) -> None:
        launched["profile_name"] = profile_name

    monkeypatch.setattr(main_module, "run_tui", fake_run_tui)

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert launched == {"profile_name": None}


def test_profile_option_is_passed_to_tui(monkeypatch) -> None:
    launched = {}

    def fake_run_tui(profile_name: str | None = None) -> None:
        launched["profile_name"] = profile_name

    monkeypatch.setattr(main_module, "run_tui", fake_run_tui)

    result = runner.invoke(app, ["--profile", "dev"])

    assert result.exit_code == 0
    assert launched == {"profile_name": "dev"}


def test_dev_command_enables_css_watch_mode(monkeypatch) -> None:
    captured = {}

    def fake_run_tui(profile_name: str | None = None, *, watch_css: bool = False) -> None:
        captured["profile_name"] = profile_name
        captured["watch_css"] = watch_css

    monkeypatch.setattr(main_module, "run_tui", fake_run_tui)

    result = runner.invoke(app, ["dev", "--profile", "dev"])

    assert result.exit_code == 0
    assert captured == {"profile_name": "dev", "watch_css": True}
