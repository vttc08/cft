import json
from datetime import datetime
import asyncio
import threading

from cft.aws.cloudfront import AccountIdentity, CloudFrontInventory
from cft.aws.cloudwatch_logs import CloudWatchLogGroupSummary
from cft.config.paths import AppPaths
from cft.data_exports import BillingSnapshot
from cft.models.cache import ProfileCacheState, SourceMetrics, StandardLogDeliveryRecord
from cft.models.distribution import DistributionSummary
from cft.tui.app import CFT_AWS_THEME, CftApp, CurExportStatus, SummaryPreviewData, SummaryWidgetShowcase
from cft.tui.screens.config_menu import ConfigurationMenuScreen
from cft.tui.screens.cwl_logs_setup import CwlLogGroupSetupScreen
from cft.tui.screens.cur_export_setup import CurExportSetupScreen
from cft.tui.screens.distribution_detail import DistributionDetailScreen
from textual.css.query import NoMatches
from textual.widgets import Button, Digits, Footer, Input, Link, ListView, ProgressBar, Select, Static


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
        standard_log_deliveries={
            "E4567890": (
                StandardLogDeliveryRecord(
                    delivery_id="delivery-1",
                    delivery_arn="arn:aws:logs:us-east-1:123456789012:delivery/delivery-1",
                    delivery_destination_arn="arn:aws:logs:us-east-1:123456789012:delivery-destination/dest-1",
                    delivery_destination_resource_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
                    delivery_destination_type="CWL",
                    delivery_source_name="CreatedByCloudFront-E4567890-ACCESS_LOGS",
                ),
            ),
            "E1234567890ABCDEFGHIJKL": (
                StandardLogDeliveryRecord(
                    delivery_id="delivery-2",
                    delivery_arn="arn:aws:logs:us-east-1:123456789012:delivery/delivery-2",
                    delivery_destination_arn="arn:aws:logs:us-east-1:123456789012:delivery-destination/dest-2",
                    delivery_destination_resource_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs-2",
                    delivery_destination_type="CWL",
                    delivery_source_name="CreatedByCloudFront-E1234567890ABCDEFGHIJKL-ACCESS_LOGS",
                ),
                StandardLogDeliveryRecord(
                    delivery_id="delivery-3",
                    delivery_arn="arn:aws:logs:us-east-1:123456789012:delivery/delivery-3",
                    delivery_destination_arn="arn:aws:s3:::cloudfront-logs",
                    delivery_destination_type="S3",
                    delivery_source_name="CreatedByCloudFront-E1234567890ABCDEFGHIJKL-ACCESS_LOGS",
                ),
            ),
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


def fake_log_groups() -> tuple[CloudWatchLogGroupSummary, ...]:
    return (
        CloudWatchLogGroupSummary(
            log_group_name="cloudfrontlogs",
            log_group_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs",
            log_group_class="INFREQUENT_ACCESS",
        ),
        CloudWatchLogGroupSummary(
            log_group_name="cloudfrontlogs2",
            log_group_arn="arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs2",
            log_group_class="STANDARD",
        ),
    )


async def wait_for_dashboard_ready(app: CftApp, pilot, *, attempts: int = 20) -> None:
    for _ in range(attempts):
        dashboard = app.query_one("#dashboard-scroll")
        table = app.query_one("#distributions")
        if not dashboard.has_class("hidden") and table.row_count == 3 and table.ordered_columns:
            return
        await pilot.pause()
    raise AssertionError("dashboard did not finish loading")


async def wait_for_onboarding_ready(app: CftApp, pilot, *, attempts: int = 20) -> None:
    for _ in range(attempts):
        try:
            modal = app.query_one("#onboarding-modal")
            title = app.query_one("#onboarding-title", Static)
        except NoMatches:
            await pilot.pause()
            continue
        if not modal.has_class("hidden") and title.content == "Welcome to cft":
            return
        await pilot.pause()
    raise AssertionError("onboarding screen did not appear")


def make_app(
    tmp_path,
    *,
    profile_name: str | None = None,
    onboarding_seen: bool = True,
    **kwargs,
) -> CftApp:
    paths = AppPaths.from_base(tmp_path / "cft")
    if onboarding_seen:
        seed_profile_name = profile_name or "default"
        paths.profile_state_file(seed_profile_name).parent.mkdir(parents=True, exist_ok=True)
        paths.profile_state_file(seed_profile_name).write_text(
            json.dumps(
                ProfileCacheState(
                    profile_name=seed_profile_name,
                    onboarding_seen=True,
                ).to_payload()
            ),
            encoding="utf-8",
        )
    return CftApp(paths=paths, profile_name=profile_name, **kwargs)


def test_tui_shows_onboarding_once_and_persists_dismissal(tmp_path) -> None:
    asyncio.run(_assert_tui_shows_onboarding_once_and_persists_dismissal(tmp_path))


async def _assert_tui_shows_onboarding_once_and_persists_dismissal(tmp_path) -> None:
    app = make_app(
        tmp_path,
        onboarding_seen=False,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await wait_for_onboarding_ready(app, pilot)
        assert app.query_one("#onboarding-title", Static).content == "Welcome to cft"
        assert "This screen appears only once" in app.query_one("#onboarding-subtitle", Static).content

        await pilot.press("enter")
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        state_payload = json.loads(app.paths.profile_state_file("default").read_text(encoding="utf-8"))
        assert state_payload["onboarding_seen"] is True

    second_app = make_app(
        tmp_path,
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with second_app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert second_app.query_one("#onboarding-modal").has_class("hidden")
        await wait_for_dashboard_ready(second_app, pilot)


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
        assert summary_note in {
            "Profile dev · Account 123456789012",
            "dev · 123456789012",
        }
        assert app.query_one("#summary-now").content in {
            "Now: 2026-05-11 09:30:00",
            "09:30:00",
        }
        assert app.query_one("#summary-last-updated").content == "Updated: -"
        assert app.query_one("#summary-configuration-title").content == "CUR Export"
        assert app.query_one("#summary-configuration-summary").content in {
            "Not configured",
            "Configured",
            "billing-bucket",
        }
        assert app.query_one("#summary-configuration-detail").content in {
            "Set up the data export link",
            "Set up the data export l..",
            "/",
        }
        assert app.query_one("#summary-configuration-action", Link).content == "Edit Configuration"
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
        assert cell_plain(table.get_row_at(1)[5]) == "CWL"
        assert cell_plain(table.get_row_at(1)[6]) == "99.89 GB"
        assert cell_plain(table.get_row_at(1)[7]) == "-"
        assert cell_plain(table.get_row_at(1)[8]) == "99.89K"
        assert cell_plain(table.get_row_at(2)[2]) == "PAYG"
        assert cell_plain(table.get_row_at(2)[4]) == "●"
        assert cell_plain(table.get_row_at(2)[5]) == "MIX"
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
        footer = app.query_one(Footer)
        assert footer is not None
        assert footer.region.height == 1
        assert footer.content_size.height == 1
        assert list(footer.children)
        assert all(child.region.y == footer.region.y for child in footer.children)
        # Verify key bindings are defined
        assert any(b.key == "ctrl+p" for b in app.BINDINGS)

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
        assert app.screen.query_one("#distribution-detail-logging-enabled", Static).content == "CWL"
        assert app.screen.query_one("#distribution-detail-logging-delivery-ids", Static).content == (
            "delivery-1"
        )
        assert app.screen.query_one("#distribution-detail-logging-destination-types", Static).content == (
            "CWL"
        )
        assert app.screen.query_one("#distribution-detail-logging-destination-arns", Static).content == (
            "arn:aws:logs:us-east-1:123456789012:delivery-destination/dest-1"
        )
        assert app.screen.query_one("#distribution-detail-logging-resource-arns", Static).content == (
            "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs"
        )
        assert app.screen.query_one("#distribution-detail-logging-source-names", Static).content == (
            "CreatedByCloudFront-E4567890-ACCESS_LOGS"
        )
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


def test_tui_merges_cloudwatch_s3_and_cwl_upload_usage() -> None:
    merged = CftApp._merge_usage_snapshots(
        CftApp._merge_usage_snapshots(
            {"E123": SourceMetrics(download=123, requests=456, month_key="2026-05")},
            {"E123": SourceMetrics(upload=789, month_key="2026-05", source_key="s3:bucket")},
        ),
        {"E123": SourceMetrics(upload=456, month_key="2026-05", source_key="manual:log-group")},
    )

    assert merged["E123"].download == 123
    assert merged["E123"].requests == 456
    assert merged["E123"].upload == 456
    assert merged["E123"].month_key == "2026-05"
    assert merged["E123"].source_key == "manual:log-group"


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
        assert {
            binding.key if hasattr(binding, "key") else binding[0]
            for binding in app.BINDINGS
        } == {"r", "ctrl+p", "b", "q", "ctrl+q", "ctrl+c"}
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
        assert app.query_one("#summary-note").content in {
            "Profile dev · Account 123456789012",
            "dev · 123456789012",
        }
        assert app.query_one("#summary-now").content in {
            "Now: 2026-05-11 09:30:00",
            "09:30:00",
        }
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
        assert notifications[-1][0].startswith("Refreshed CloudWatch usage and logs at ")


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

        assert app.query_one("#summary-configuration-title").content == "CUR Export"
        assert app.query_one("#summary-configuration-summary").content == "billing-bucket"
        assert app.query_one("#summary-configuration-detail").content == "/exports"
        assert app.query_one("#summary-configuration-action", Link).content == "Edit Configuration"
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

        summary_button = app.query_one("#summary-configuration-action", Link)
        summary_button.focus()
        await pilot.press("ctrl+p")
        await pilot.pause()

        assert isinstance(app.screen, ConfigurationMenuScreen)
        assert app.screen.query_one("#configuration-menu-title").content == (
            "Edit Configuration for profile dev"
        )
        assert app.screen.query_one("#configuration-menu-edit-cur-export", Button)
        assert app.screen.query_one("#configuration-menu-edit-cwl", Button)
        await pilot.click("#configuration-menu-edit-cur-export")
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
        cur_export_link = app.query_one("#summary-configuration-action", Link)
        assert app.query_one("#summary-configuration-title").content == "CUR Export"
        assert app.query_one("#summary-configuration-summary").content == "billing-bucket"
        assert app.query_one("#summary-configuration-detail").content == "/exports/root"
        assert cur_export_link.content == "Edit Configuration"


def test_tui_cwl_log_group_setup_flow_persists_selection(tmp_path) -> None:
    asyncio.run(_assert_tui_cwl_log_group_setup_flow_persists_selection(tmp_path))


async def _assert_tui_cwl_log_group_setup_flow_persists_selection(tmp_path) -> None:
    paths = AppPaths.from_base(tmp_path / "cft")
    app = make_app(
        tmp_path,
        profile_name="dev",
        inventory_loader=fake_inventory,
        usage_loader=fake_usage,
        log_group_loader=fake_log_groups,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await wait_for_dashboard_ready(app, pilot)

        summary_button = app.query_one("#summary-configuration-action", Link)
        summary_button.focus()
        await pilot.press("ctrl+p")
        await pilot.pause()

        assert isinstance(app.screen, ConfigurationMenuScreen)
        await pilot.click("#configuration-menu-edit-cwl")
        await pilot.pause()

        assert isinstance(app.screen, CwlLogGroupSetupScreen)
        log_group_list = app.screen.query_one("#cwl-log-group-list", ListView)
        assert log_group_list.index == 0

        await pilot.press("enter")
        await pilot.pause()

        save_button = app.screen.query_one("#cwl-log-group-save", Button)
        assert not save_button.disabled
        await pilot.click("#cwl-log-group-save")
        await pilot.pause()

        profile_text = paths.profile_config_file("dev").read_text(encoding="utf-8")
        assert (
            'cwl_log_group = "arn:aws:logs:us-east-1:123456789012:log-group:cloudfrontlogs"'
            in profile_text
        )
        cwl_log_group_link = app.query_one("#summary-configuration-action", Link)
        assert app.query_one("#summary-configuration-title").content == "CUR Export"
        assert app.query_one("#summary-configuration-summary").content == "Not configured"
        assert app.query_one("#summary-configuration-detail").content in {
            "Set up the data export link",
            "Set up the data export l..",
        }
        assert cwl_log_group_link.content == "Edit Configuration"


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
    assert widget._format_configuration_summary(80) == "billing-bucket"
    assert widget._format_configuration_detail(80) == "/exports"
    assert widget._format_configuration_detail(25).startswith("/exports")


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

        await pilot.press("ctrl+p")
        await pilot.pause()

        assert isinstance(app.screen, ConfigurationMenuScreen)
        await pilot.click("#configuration-menu-edit-cur-export")
        await pilot.pause()

        assert app.screen.query_one("#cur-export-error").content == "Bucket discovery failed: AccessDenied"
        assert app.screen.query_one("#cur-export-empty").content == "No S3 buckets available for this profile."
        assert app.screen.query_one("#cur-export-save", Button).disabled
