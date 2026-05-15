import json
from datetime import datetime
import asyncio
import threading

from cft.aws.cloudfront import AccountIdentity, CloudFrontInventory
from cft.config.paths import AppPaths
from cft.data_exports import BillingSnapshot
from cft.models.cache import SourceMetrics
from cft.models.distribution import DistributionSummary
from cft.tui.app import CFT_AWS_THEME, CftApp, CurExportStatus, SummaryPreviewData, SummaryWidgetShowcase
from cft.tui.screens.cur_export_setup import CurExportSetupScreen
from cft.tui.screens.distribution_detail import DistributionDetailScreen
from textual.widgets import Button, Digits, Input, Link, ListView, ProgressBar, Select, Static


def cell_text(value: object) -> str:
    return str(value)


def cell_plain(value: object) -> str:
    return cell_text(value).strip()


def transfer_signature(value: object) -> tuple[str, int]:
    number, unit = cell_plain(value).split()
    decimals = len(number.partition(".")[2])
    return unit, decimals


def fake_inventory() -> CloudFrontInventory:
    return CloudFrontInventory(
        profile_name="dev",
        identity=AccountIdentity(
            account_id="123456789012",
            arn="arn:aws:iam::123456789012:user/test",
            user_id="AIDA",
        ),
        distributions=(
            DistributionSummary(
                distribution_id="E123",
                arn="arn:aws:cloudfront::123456789012:distribution/E123",
                comment="site",
                domain_name="d111.cloudfront.net",
                enabled=True,
                status="Deployed",
                aliases=(),
                origins=(),
                last_modified_time=None,
            ),
            DistributionSummary(
                distribution_id="E4567890",
                arn="arn:aws:cloudfront::123456789012:distribution/E4567890",
                comment="marketing",
                domain_name="d222.cloudfront.net",
                enabled=False,
                status="InProgress",
                aliases=("cdn.example.com",),
                origins=("origin.example.com",),
                last_modified_time=datetime(2026, 5, 10, 8, 15),
            ),
            DistributionSummary(
                distribution_id="E1234567890ABCDEFGHIJKL",
                arn="arn:aws:cloudfront::123456789012:distribution/E1234567890ABCDEFGHIJKL",
                comment="primary-marketing-site-with-a-very-long-comment",
                domain_name="d111111111111111111111111111111111111.cloudfront.net",
                enabled=True,
                status="Deployed",
                aliases=(),
                origins=(),
                last_modified_time=None,
            ),
        ),
        distribution_types={
            "E123": "Free",
            "E4567890": "PAYG",
            "E1234567890ABCDEFGHIJKL": "PAYG",
        },
    )


def fake_usage(_: CloudFrontInventory) -> dict[str, SourceMetrics]:
    return {
        "E123": SourceMetrics(download=1_234_000_000, requests=1_234, month_key="2026-05"),
        "E4567890": SourceMetrics(download=99_890_000_000, requests=99_890, month_key="2026-05"),
        "E1234567890ABCDEFGHIJKL": SourceMetrics(
            download=998_900_000_000,
            requests=1_234_000,
            month_key="2026-05",
        ),
    }


def fake_billing() -> BillingSnapshot:
    return BillingSnapshot(
        profile_name="dev",
        configured=True,
        download_bytes=128_400_000_000,
        upload_bytes=6_800_000_000,
        requests=1_240_000,
        cost=8.42,
        data_end=datetime(2026, 5, 11, 8, 0),
    )


async def wait_for_dashboard_ready(app: CftApp, pilot, *, attempts: int = 20) -> None:
    for _ in range(attempts):
        dashboard = app.query_one("#dashboard-scroll")
        table = app.query_one("#distributions")
        if not dashboard.has_class("hidden") and table.row_count == 3 and table.ordered_columns:
            return
        await pilot.pause()
    raise AssertionError("dashboard did not finish loading")


def make_app(tmp_path, **kwargs) -> CftApp:
    return CftApp(paths=AppPaths.from_base(tmp_path / "cft"), **kwargs)


def test_tui_renders_summary_and_distribution_table(tmp_path) -> None:
    asyncio.run(_assert_tui_renders_summary_and_distribution_table(tmp_path))


async def _assert_tui_renders_summary_and_distribution_table(tmp_path) -> None:
    app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        assert app.query_one("#dashboard-scroll")
        assert app.query_one("#summary-showcase")
        summary_note = app.query_one("#summary-note").content
        assert summary_note.startswith("Profile dev · Account 123456789012")
        assert app.query_one("#summary-now").content == "Now: 2026-05-11 09:30:00"
        assert app.query_one("#summary-last-updated").content == "Updated: -"
        cur_export_link = app.query_one("#summary-cur-export-action", Link)
        assert app.query_one("#summary-cur-export-title").content == "CUR Export"
        assert app.query_one("#summary-cur-export-bucket").content == "Not configured"
        assert app.query_one("#summary-cur-export-detail").content == "Path: / · Export: -"
        assert cur_export_link.content == "Setup Data Export"
        assert app.query_one("#summary-download-value").content == "-"
        assert app.query_one("#summary-upload-value").content == "-"
        assert app.query_one("#summary-requests-value").content == "-"
        assert app.query_one("#summary-cost-prefix").content == "$"
        assert app.query_one("#summary-cost-value", Digits).value == "-"
        assert round(app.query_one("#summary-download-card-bar", ProgressBar).progress, 2) == 0
        assert round(app.query_one("#summary-upload-card-bar", ProgressBar).progress, 2) == 0
        assert app.query_one("#summary-requests-card-bar", ProgressBar).progress == 0
        assert round(app.query_one("#summary-requests-card-bar", ProgressBar).total or 0) == 10_000_000
        table = app.query_one("#distributions")
        assert table.ordered_columns[0].label.plain.strip() == "ID"
        assert [column.label.plain.strip() for column in table.ordered_columns] == [
            "ID",
            "Comment",
            "Type",
            "URL",
            "On",
            "Log",
            "Down",
            "Up",
            "Req",
        ]
        assert table.row_count == 3
        assert cell_plain(table.get_row_at(0)[2]) == "Free"
        assert cell_plain(table.get_row_at(0)[4]) == "●"
        assert cell_plain(table.get_row_at(0)[5]) == "-"
        assert cell_plain(table.get_row_at(0)[6]) == "1.23 GB"
        assert cell_plain(table.get_row_at(0)[7]) == "-"
        assert cell_plain(table.get_row_at(0)[8]) == "1.23K"
        assert cell_plain(table.get_row_at(1)[2]) == "PAYG"
        assert cell_plain(table.get_row_at(1)[4]) == "○"
        assert cell_plain(table.get_row_at(1)[5]) == "-"
        assert cell_plain(table.get_row_at(1)[6]) == "99.89 GB"
        assert cell_plain(table.get_row_at(1)[7]) == "-"
        assert cell_plain(table.get_row_at(1)[8]) == "99.89K"
        assert cell_plain(table.get_row_at(2)[2]) == "PAYG"
        assert cell_plain(table.get_row_at(2)[4]) == "●"
        assert cell_plain(table.get_row_at(2)[5]) == "-"
        assert cell_plain(table.get_row_at(2)[6]) == "998.90 GB"
        assert cell_plain(table.get_row_at(2)[7]) == "-"
        assert cell_plain(table.get_row_at(2)[8]) == "1.23M"
        assert len(cell_plain(table.get_row_at(0)[4])) == 1
        assert len(cell_plain(table.get_row_at(0)[5])) == 1
        assert transfer_signature(table.get_row_at(0)[6]) == ("GB", 2)
        assert transfer_signature(table.get_row_at(1)[6]) == ("GB", 2)
        assert transfer_signature(table.get_row_at(2)[6]) == ("GB", 2)
        assert cell_text(table.get_row_at(1)[7]).startswith(" ")
        assert cell_text(table.get_row_at(2)[7]).startswith(" ")
        assert cell_text(table.get_row_at(0)[6]).endswith(" ")
        assert table.ordered_columns[2].width >= 5
        assert table.ordered_columns[3].width >= 7
        assert table.ordered_columns[6].width >= 9
        assert table.ordered_columns[7].width >= 9
        assert table.ordered_columns[8].width >= 5
        assert 5 <= table.ordered_columns[0].width <= 15

        table.focus()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, DistributionDetailScreen)
        assert app.screen.query_one("#distribution-detail-title", Static).content == "marketing"
        assert app.screen.query_one("#distribution-detail-subtitle", Static).content == "E4567890"
        assert app.screen.query_one("#distribution-detail-id", Static).content == "E4567890"
        assert app.screen.query_one("#distribution-detail-type", Select).value == "PAYG"
        assert app.screen.query_one("#distribution-detail-domain", Static).content == "d222.cloudfront.net"
        assert app.screen.query_one("#distribution-detail-status", Static).content == "InProgress"
        assert app.screen.query_one("#distribution-detail-enabled", Static).content == "No"
        assert app.screen.query_one("#distribution-detail-aliases", Static).content == "cdn.example.com"
        assert app.screen.query_one("#distribution-detail-origins", Static).content == "origin.example.com"
        assert app.screen.query_one("#distribution-detail-last-modified", Static).content == (
            "2026-05-10 08:15:00"
        )
        assert app.screen.query_one("#distribution-detail-download", Static).content == "99.89 GB"
        assert app.screen.query_one("#distribution-detail-upload", Static).content == "-"
        assert app.screen.query_one("#distribution-detail-requests", Static).content == "99,890"
        assert app.screen.query_one("#distribution-detail-month", Static).content == "2026-05"
        assert app.screen.query_one("#distribution-detail-cancel", Button)
        assert app.screen.query_one("#distribution-detail-save", Button)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, DistributionDetailScreen)


def test_tui_updates_active_distribution_preview_with_arrow_keys(tmp_path) -> None:
    asyncio.run(_assert_tui_updates_active_distribution_preview_with_arrow_keys(tmp_path))


async def _assert_tui_updates_active_distribution_preview_with_arrow_keys(tmp_path) -> None:
    app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        table = app.query_one("#distributions")
        assert app.query_one("#distribution-preview-title", Static).content == (
            "Active: site · E123 · Free"
        )
        assert app.query_one("#distribution-preview-download-value", Static).content == (
            "1.23 GB / 100 GB"
        )
        assert app.query_one("#distribution-preview-upload-value", Static).content == "n/a"
        assert app.query_one("#distribution-preview-requests-value", Static).content == (
            "1.23K / 1,000,000"
        )
        assert round(app.query_one("#distribution-preview-download-bar", ProgressBar).progress, 2) == 1_234_000_000
        assert round(app.query_one("#distribution-preview-download-bar", ProgressBar).total or 0) == 100_000_000_000
        assert round(app.query_one("#distribution-preview-requests-bar", ProgressBar).progress, 2) == 1234
        assert round(app.query_one("#distribution-preview-requests-bar", ProgressBar).total or 0) == 1_000_000
        assert round(app.query_one("#distribution-preview-upload-bar", ProgressBar).progress, 2) == 0

        table.focus()
        await pilot.press("down")
        await pilot.pause()

        assert app.query_one("#distribution-preview-title", Static).content == (
            "Active: marketing · E4567890 · PAYG"
        )
        assert app.query_one("#distribution-preview-download-value", Static).content == (
            "99.89 GB / 1024 GB"
        )
        assert app.query_one("#distribution-preview-upload-value", Static).content == (
            "n/a / 0.249 GB"
        )
        assert app.query_one("#distribution-preview-requests-value", Static).content == (
            "99.89K / 10,000,000"
        )
        assert round(app.query_one("#distribution-preview-download-bar", ProgressBar).total or 0) == 1_024_000_000_000
        assert round(app.query_one("#distribution-preview-download-bar", ProgressBar).progress, 2) == 99_890_000_000
        assert round(app.query_one("#distribution-preview-requests-bar", ProgressBar).total or 0) == 10_000_000
        assert round(app.query_one("#distribution-preview-requests-bar", ProgressBar).progress, 2) == 99_890

        await pilot.press("down")
        await pilot.pause()

        assert app.query_one("#distribution-preview-title", Static).content == (
            "Active: primary-marketing-site-with-a-very-long-comment · E1234567890ABCDEFGHIJKL · PAYG"
        )
        assert app.query_one("#distribution-preview-download-value", Static).content == (
            "998.90 GB / 1024 GB"
        )
        assert app.query_one("#distribution-preview-upload-value", Static).content == (
            "n/a / 0.249 GB"
        )
        assert app.query_one("#distribution-preview-requests-value", Static).content == (
            "1.23M / 10,000,000"
        )


def test_tui_updates_distribution_plan_type_and_caches_it(tmp_path) -> None:
    asyncio.run(_assert_tui_updates_distribution_plan_type_and_caches_it(tmp_path))


async def _assert_tui_updates_distribution_plan_type_and_caches_it(tmp_path) -> None:
    app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        table = app.query_one("#distributions")
        assert cell_plain(table.get_row_at(1)[2]) == "PAYG"

        table.focus()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        select = app.screen.query_one("#distribution-detail-type", Select)
        assert select.value == "PAYG"

        select.value = "Free"
        await pilot.pause()
        assert select.value == "Free"

        await pilot.press("tab")
        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.pause()

        table = app.query_one("#distributions")
        assert cell_plain(table.get_row_at(1)[2]) == "Free"

        payload = json.loads(app.paths.profile_state_file("dev").read_text(encoding="utf-8"))
        assert payload["distributions"]["E4567890"]["type"] == "Free"


def test_tui_shows_loading_panel_while_refreshing_data(tmp_path) -> None:
    asyncio.run(_assert_tui_shows_loading_panel_while_refreshing_data(tmp_path))


async def _assert_tui_shows_loading_panel_while_refreshing_data(tmp_path) -> None:
    gate = threading.Event()

    def slow_inventory_loader() -> CloudFrontInventory:
        gate.wait(timeout=3)
        return fake_inventory()

    def slow_usage_loader(_: CloudFrontInventory) -> dict[str, SourceMetrics]:
        return fake_usage(fake_inventory())

    app = make_app(
        tmp_path,
        inventory_loader=slow_inventory_loader,
        usage_loader=slow_usage_loader,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        loading_panel = app.query_one("#loading-panel")
        dashboard = app.query_one("#dashboard-scroll")

        assert not loading_panel.has_class("hidden")
        assert dashboard.has_class("hidden")

        gate.set()
        for _ in range(20):
            await pilot.pause()
            if loading_panel.has_class("hidden") and not dashboard.has_class("hidden"):
                break

        assert loading_panel.has_class("hidden")
        assert not dashboard.has_class("hidden")
        assert app.query_one("#distributions").row_count == 3


def test_tui_uses_custom_aws_theme(tmp_path) -> None:
    asyncio.run(_assert_tui_uses_custom_aws_theme(tmp_path))


async def _assert_tui_uses_custom_aws_theme(tmp_path) -> None:
    app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        assert app.theme == CFT_AWS_THEME.name
        assert {binding[0] for binding in app.BINDINGS} == {"r", "b", "q", "ctrl+q", "ctrl+c"}
        active_theme = app.current_theme
        assert active_theme.name == CFT_AWS_THEME.name
        assert active_theme.primary == "#FF9900"
        assert active_theme.secondary == "#8C4FFF"
        assert active_theme.accent == "#FF9900"
        assert active_theme.background == "#171A1F"
        assert active_theme.surface == "#1F2329"


def test_tui_truncates_long_distribution_fields_to_fit_narrow_terminal(tmp_path) -> None:
    asyncio.run(_assert_tui_truncates_long_distribution_fields_to_fit_narrow_terminal(tmp_path))


async def _assert_tui_truncates_long_distribution_fields_to_fit_narrow_terminal(tmp_path) -> None:
    app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        table = app.query_one("#distributions")
        row = table.get_row_at(2)

        assert cell_plain(row[0]).endswith("..")
        assert cell_plain(row[1]).endswith("..")
        assert cell_plain(row[3]).endswith("..")
        assert transfer_signature(row[6])[0] in {"GB", "G"}
        assert cell_plain(row[7]) == "-"
        assert cell_plain(row[8]) == "1.23M"
        assert cell_text(row[1]).endswith(" ")
        assert cell_text(row[3]).endswith(" ")
        assert table.virtual_size.width <= table.size.width


def test_tui_remains_keyboard_accessible_on_short_terminals(tmp_path) -> None:
    asyncio.run(_assert_tui_remains_keyboard_accessible_on_short_terminals(tmp_path))


async def _assert_tui_remains_keyboard_accessible_on_short_terminals(tmp_path) -> None:
    app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(80, 14)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        assert app.query_one("#dashboard-scroll")
        assert app.query_one("#summary-showcase")
        assert app.query_one("#summary-note").content == "dev · 123456789012"
        assert app.query_one("#summary-now").content == "Now: 2026-05-11 09:30:00"
        assert app.query_one("#summary-last-updated").content == "Updated: -"
        table = app.query_one("#distributions")
        table.focus()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, DistributionDetailScreen)
        assert app.screen.query_one("#distribution-detail-id", Static).content == "E4567890"


def test_tui_refresh_action_reloads_usage_data(tmp_path) -> None:
    asyncio.run(_assert_tui_refresh_action_reloads_usage_data(tmp_path))


async def _assert_tui_refresh_action_reloads_usage_data(tmp_path) -> None:
    inventory_calls = {"count": 0}
    usage_calls = {"count": 0}
    notifications: list[tuple[str, str | None]] = []

    def inventory_loader() -> CloudFrontInventory:
        inventory_calls["count"] += 1
        return fake_inventory()

    def usage_loader(_: CloudFrontInventory) -> dict[str, SourceMetrics]:
        usage_calls["count"] += 1
        if usage_calls["count"] == 1:
            return fake_usage(fake_inventory())
        return {
            "E123": SourceMetrics(download=2_000_000_000, requests=2_000, month_key="2026-05"),
            "E4567890": SourceMetrics(download=3_000_000_000, requests=3_000, month_key="2026-05"),
            "E1234567890ABCDEFGHIJKL": SourceMetrics(
                download=4_000_000_000,
                requests=4_000,
                month_key="2026-05",
            ),
        }

    app = make_app(
        tmp_path,
        inventory_loader=inventory_loader,
        usage_loader=usage_loader,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )
    app.notify = lambda message, *, title=None, severity=None, timeout=None: notifications.append(  # type: ignore[assignment]
        (message, title)
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        table = app.query_one("#distributions")
        assert cell_plain(table.get_row_at(0)[6]) == "1.23 GB"
        assert inventory_calls["count"] == 1
        assert usage_calls["count"] == 1

        await pilot.press("r")
        for _ in range(20):
            await pilot.pause()
            if cell_plain(table.get_row_at(0)[6]) == "2.00 GB":
                break

        assert inventory_calls["count"] == 2
        assert usage_calls["count"] == 2
        assert cell_plain(table.get_row_at(0)[6]) == "2.00 GB"
        assert cell_plain(table.get_row_at(1)[6]) == "3.00 GB"
        assert notifications
        assert notifications[-1][0].startswith("Refreshed CloudWatch usage at ")


def test_tui_recomputes_column_widths_after_terminal_resize(tmp_path) -> None:
    asyncio.run(_assert_tui_recomputes_column_widths_after_terminal_resize(tmp_path))


async def _assert_tui_recomputes_column_widths_after_terminal_resize(tmp_path) -> None:
    app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        table = app.query_one("#distributions")
        initial_widths = [column.width for column in table.ordered_columns]

        await pilot.resize_terminal(60, 30)
        await pilot.pause()

        resized_widths = [column.width for column in table.ordered_columns]

        assert resized_widths[0] <= 7
        assert resized_widths[1] <= initial_widths[1]
        assert resized_widths[3] <= initial_widths[3]
        assert resized_widths[6] <= initial_widths[6]
        assert resized_widths[7] <= initial_widths[7]
        assert resized_widths[8] <= initial_widths[8]
        assert table.virtual_size.width <= table.size.width


def test_summary_progress_bars_resize_with_terminal_width(tmp_path) -> None:
    asyncio.run(_assert_summary_progress_bars_resize_with_terminal_width(tmp_path))


async def _assert_summary_progress_bars_resize_with_terminal_width(tmp_path) -> None:
    app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        download_card = app.query_one("#summary-download-card")
        download_bar = app.query_one("#summary-download-card-bar", ProgressBar)

        initial_card_width = download_card.size.width
        initial_bar_width = download_bar.size.width

        await pilot.resize_terminal(60, 30)
        await pilot.pause()

        resized_card_width = download_card.size.width
        resized_bar_width = download_bar.size.width

        assert resized_card_width < initial_card_width
        assert resized_bar_width < initial_bar_width
        assert resized_bar_width <= resized_card_width

        await pilot.resize_terminal(100, 30)
        await pilot.pause()

        assert download_card.size.width == initial_card_width
        assert download_bar.size.width == initial_bar_width


def test_tui_formats_transfer_columns_with_shared_precision() -> None:
    spec = CftApp._resolve_transfer_format(
        (1_234_000_000, 99_890_000_000, 998_900_000_000),
        width=9,
    )
    assert spec.suffix == " GB"
    assert spec.decimals == 2
    assert CftApp._format_transfer_value(1_234_000_000, spec) == "1.23 GB"
    assert CftApp._format_transfer_value(99_890_000_000, spec) == "99.89 GB"
    assert CftApp._format_transfer_value(998_900_000_000, spec) == "998.90 GB"
    assert CftApp._format_transfer_value(1_234_000_000.0, spec) == "1.23 GB"


def test_tui_formats_request_counts_compactly() -> None:
    assert CftApp._format_request_count(1_234, width=6) == "1.23K"
    assert CftApp._format_request_count(99_890, width=7) == "99.89K"
    assert CftApp._format_request_count(1_234_000, width=6) == "1.23M"
    assert CftApp._format_request_count(987, width=4) == "987"
    assert CftApp._format_request_count(1_234_000.0, width=6) == "1.23M"


def test_tui_formats_summary_transfer_values_from_bytes() -> None:
    assert SummaryWidgetShowcase._format_summary_transfer(128_400_000_000) == "128.4 GB"
    assert SummaryWidgetShowcase._format_summary_transfer(6_800_000_000.0) == "6.8 GB"
    assert SummaryWidgetShowcase._format_summary_transfer(None) == "-"


def test_tui_shows_configured_cur_export_status(tmp_path) -> None:
    asyncio.run(_assert_tui_shows_configured_cur_export_status(tmp_path))


async def _assert_tui_shows_configured_cur_export_status(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    paths.ensure_base_dirs()
    paths.profile_config_file("dev").write_text(
        """
[data_export]
bucket = "billing-bucket"
prefix = "exports"
export_name = "cloudfront-cur"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    app = make_app(
        tmp_path,
        profile_name="dev",
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        billing_loader=fake_billing,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        cur_export_link = app.query_one("#summary-cur-export-action", Link)
        assert app.query_one("#summary-cur-export-title").content == "CUR Export"
        assert app.query_one("#summary-cur-export-bucket").content == "billing-bucket"
        assert app.query_one("#summary-cur-export-detail").content == (
            "Path: /exports · Export: cloudfront-cur"
        )
        assert cur_export_link.content == "Edit Data Export"
        assert app.query_one("#summary-last-updated").content == "Updated: 2026-05-11 08:00:00"
        assert app.query_one("#summary-download-value").content == "128.4 GB"
        assert app.query_one("#summary-upload-value").content == "6.8 GB"
        assert app.query_one("#summary-requests-value").content == "1.24M"
        assert app.query_one("#summary-cost-value", Digits).value == "8.42"


def test_tui_cur_export_setup_flow_persists_selection(tmp_path) -> None:
    asyncio.run(_assert_tui_cur_export_setup_flow_persists_selection(tmp_path))


async def _assert_tui_cur_export_setup_flow_persists_selection(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    app = make_app(
        tmp_path,
        profile_name="dev",
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        bucket_loader=lambda: ("alpha-bucket", "billing-bucket", "zeta-bucket"),
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        summary_button = app.query_one("#summary-cur-export-action", Link)
        summary_button.focus()
        await pilot.press("b")
        await pilot.pause()

        assert isinstance(app.screen, CurExportSetupScreen)
        bucket_list = app.screen.query_one("#cur-export-bucket-list", ListView)
        assert bucket_list.index == 0

        await pilot.press("down")
        await pilot.press("tab")
        await pilot.pause()

        export_name = app.screen.query_one("#cur-export-export-name", Input)
        export_name.value = "cloudfront-cur"
        await pilot.press("shift+tab")
        prefix = app.screen.query_one("#cur-export-prefix", Input)
        prefix.value = "/exports/root/"
        await pilot.press("tab")
        await pilot.press("tab")
        await pilot.pause()

        save_button = app.screen.query_one("#cur-export-save", Button)
        assert not save_button.disabled
        await pilot.click("#cur-export-save")
        await pilot.pause()

        profile_text = paths.profile_config_file("dev").read_text(encoding="utf-8")
        assert 'bucket = "billing-bucket"' in profile_text
        assert 'prefix = "exports/root"' in profile_text
        assert 'export_name = "cloudfront-cur"' in profile_text
        cur_export_link = app.query_one("#summary-cur-export-action", Link)
        assert app.query_one("#summary-cur-export-bucket").content == "billing-bucket"
        assert app.query_one("#summary-cur-export-detail").content == (
            "Path: /exports/root · Export: cloudfront-cur"
        )
        assert cur_export_link.content == "Edit Data Export"


def test_summary_widget_truncation_stages_are_consistent() -> None:
    widget = SummaryWidgetShowcase(
        profile_name="dev",
        data=SummaryPreviewData(
            profile_name="dev",
            account_id="123456789012",
            download_bytes=0,
            upload_bytes=0,
            cost=0,
            requests=0,
        ),
        account_id="123456789012",
    )
    timestamp = datetime(2026, 5, 11, 9, 30)

    assert widget._format_profile_account_line(80) == "Profile dev · Account 123456789012"
    assert widget._format_profile_account_line(18) == "dev · 123456789012"
    assert widget._format_timestamp_line("Now", timestamp, 80) == "Now: 2026-05-11 09:30:00"
    assert widget._format_timestamp_line("Now", timestamp, 11) == "05-11 09:30"
    assert widget._format_timestamp_line("Now", timestamp, 8) == "09:30:00"
    widget.cur_export_status = CurExportStatus(
        bucket="billing-bucket",
        prefix="exports",
        export_name="cloudfront-cur",
    )
    assert widget._format_cur_export_bucket(80) == "billing-bucket"
    assert widget._format_cur_export_detail(80) == "Path: /exports · Export: cloudfront-cur"
    assert widget._format_cur_export_detail(25) == "/exports · cloudfront-cur"


def test_tui_cur_export_setup_shows_bucket_discovery_error(tmp_path) -> None:
    asyncio.run(_assert_tui_cur_export_setup_shows_bucket_discovery_error(tmp_path))


async def _assert_tui_cur_export_setup_shows_bucket_discovery_error(tmp_path) -> None:
    app = make_app(
        tmp_path,
        profile_name="dev",
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        bucket_loader=lambda: (_ for _ in ()).throw(RuntimeError("AccessDenied")),
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        await pilot.press("b")
        await pilot.pause()

        assert app.screen.query_one("#cur-export-error").content == "Bucket discovery failed: AccessDenied"
        assert app.screen.query_one("#cur-export-empty").content == "No S3 buckets available for this profile."
        assert app.screen.query_one("#cur-export-save", Button).disabled
