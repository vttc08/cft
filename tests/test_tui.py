from datetime import datetime
import asyncio

from cft.aws.cloudfront import AccountIdentity, CloudFrontInventory
from cft.models.distribution import DistributionSummary
from cft.tui.app import CFT_AWS_THEME, MOCK_SUMMARY_DATA, CftApp
from textual.widgets import Digits


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
                aliases=(),
                origins=(),
                last_modified_time=None,
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
    )


def test_tui_renders_summary_and_distribution_table() -> None:
    asyncio.run(_assert_tui_renders_summary_and_distribution_table())


async def _assert_tui_renders_summary_and_distribution_table() -> None:
    app = CftApp(
        inventory_loader=fake_inventory,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        assert app.query_one("#dashboard-scroll")
        assert app.query_one("#summary-showcase")
        assert app.query_one("#summary-note").content == "Profile default · Account 123456789012"
        assert app.query_one("#summary-download-value").content == MOCK_SUMMARY_DATA.download
        assert app.query_one("#summary-upload-value").content == MOCK_SUMMARY_DATA.upload
        assert app.query_one("#summary-requests-value").content == MOCK_SUMMARY_DATA.requests
        assert app.query_one("#summary-cost-prefix").content == "$"
        assert app.query_one("#summary-cost-value", Digits).value == "8.42"
        assert round(app.query_one("#summary-budget-bar").progress, 2) == MOCK_SUMMARY_DATA.budget_progress
        assert tuple(app.query_one("#summary-download-trend").data) == MOCK_SUMMARY_DATA.download_trend
        assert tuple(app.query_one("#summary-upload-trend").data) == MOCK_SUMMARY_DATA.upload_trend
        assert app.query_one("#table-title").content == "Distributions"
        assert app.query_one("#table-subtitle").content == "Current month operational view"

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
        assert cell_plain(table.get_row_at(0)[5]) == "·"
        assert cell_plain(table.get_row_at(0)[6]) == "1.23 GB"
        assert cell_plain(table.get_row_at(0)[7]) == "1.20 GB"
        assert cell_plain(table.get_row_at(0)[8]) == "1.23K"
        assert cell_plain(table.get_row_at(1)[2]) == "PAYG"
        assert cell_plain(table.get_row_at(1)[4]) == "○"
        assert cell_plain(table.get_row_at(1)[5]) == "·"
        assert cell_plain(table.get_row_at(1)[6]) == "99.89 GB"
        assert cell_plain(table.get_row_at(1)[7]) == "1.20 GB"
        assert cell_plain(table.get_row_at(1)[8]) == "99.89K"
        assert cell_plain(table.get_row_at(2)[2]) == "?"
        assert cell_plain(table.get_row_at(2)[4]) == "●"
        assert cell_plain(table.get_row_at(2)[5]) == "·"
        assert cell_plain(table.get_row_at(2)[6]) == "998.90 GB"
        assert cell_plain(table.get_row_at(2)[7]) == "1.23 GB"
        assert cell_plain(table.get_row_at(2)[8]) == "1.23M"
        assert len(cell_plain(table.get_row_at(0)[4])) == 1
        assert len(cell_plain(table.get_row_at(0)[5])) == 1
        assert transfer_signature(table.get_row_at(0)[6]) == ("GB", 2)
        assert transfer_signature(table.get_row_at(0)[7]) == ("GB", 2)
        assert transfer_signature(table.get_row_at(1)[6]) == ("GB", 2)
        assert transfer_signature(table.get_row_at(1)[7]) == ("GB", 2)
        assert transfer_signature(table.get_row_at(2)[6]) == ("GB", 2)
        assert transfer_signature(table.get_row_at(2)[7]) == ("GB", 2)
        assert cell_text(table.get_row_at(1)[7]).startswith(" ")
        assert cell_text(table.get_row_at(2)[7]).startswith(" ")
        assert cell_text(table.get_row_at(0)[6]).endswith(" ")
        assert table.ordered_columns[2].width >= 5
        assert table.ordered_columns[3].width >= 7
        assert table.ordered_columns[6].width >= 9
        assert table.ordered_columns[7].width >= 9
        assert table.ordered_columns[8].width >= 5
        assert 5 <= table.ordered_columns[0].width <= 7

        table.focus()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert app.query_one("#status").content == "Selected distribution E4567890: marketing"


def test_tui_uses_custom_aws_theme() -> None:
    asyncio.run(_assert_tui_uses_custom_aws_theme())


async def _assert_tui_uses_custom_aws_theme() -> None:
    app = CftApp(
        inventory_loader=fake_inventory,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        assert app.theme == CFT_AWS_THEME.name
        assert {binding[0] for binding in app.BINDINGS} == {"q", "ctrl+q", "ctrl+c"}
        active_theme = app.current_theme
        assert active_theme.name == CFT_AWS_THEME.name
        assert active_theme.primary == "#FF9900"
        assert active_theme.secondary == "#8C4FFF"
        assert active_theme.accent == "#FF9900"
        assert active_theme.background == "#171A1F"
        assert active_theme.surface == "#1F2329"


def test_tui_truncates_long_distribution_fields_to_fit_narrow_terminal() -> None:
    asyncio.run(_assert_tui_truncates_long_distribution_fields_to_fit_narrow_terminal())


async def _assert_tui_truncates_long_distribution_fields_to_fit_narrow_terminal() -> None:
    app = CftApp(
        inventory_loader=fake_inventory,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(60, 30)) as pilot:
        await pilot.pause()

        table = app.query_one("#distributions")
        row = table.get_row_at(2)

        assert cell_plain(row[0]).endswith("...")
        assert cell_plain(row[1]).endswith("...")
        assert cell_plain(row[3]).endswith("...")
        assert transfer_signature(row[6]) == transfer_signature(row[7])
        assert cell_plain(row[8]) == "1.23M"
        assert cell_text(row[1]).endswith(" ")
        assert cell_text(row[3]).endswith(" ")
        assert table.virtual_size.width <= table.size.width


def test_tui_remains_keyboard_accessible_on_short_terminals() -> None:
    asyncio.run(_assert_tui_remains_keyboard_accessible_on_short_terminals())


async def _assert_tui_remains_keyboard_accessible_on_short_terminals() -> None:
    app = CftApp(
        inventory_loader=fake_inventory,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(80, 14)) as pilot:
        await pilot.pause()

        assert app.query_one("#dashboard-scroll")
        assert app.query_one("#summary-showcase")
        assert app.query_one("#summary-note").content == "Profile default · Account 123456789012"
        table = app.query_one("#distributions")
        table.focus()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert app.query_one("#status").content == "Selected distribution E4567890: marketing"


def test_tui_recomputes_column_widths_after_terminal_resize() -> None:
    asyncio.run(_assert_tui_recomputes_column_widths_after_terminal_resize())


async def _assert_tui_recomputes_column_widths_after_terminal_resize() -> None:
    app = CftApp(
        inventory_loader=fake_inventory,
        now=lambda: datetime(2026, 5, 11, 9, 30),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

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


def test_tui_formats_transfer_columns_with_shared_precision() -> None:
    spec = CftApp._resolve_transfer_format(
        ((1_234_000_000, 1_200_000_000), (99_890_000_000, 1_200_000_000), (998_900_000_000, 1_234_000_000)),
        width=9,
    )
    assert spec.suffix == " GB"
    assert spec.decimals == 2
    assert CftApp._format_transfer_value(1_234_000_000, spec) == "1.23 GB"
    assert CftApp._format_transfer_value(99_890_000_000, spec) == "99.89 GB"
    assert CftApp._format_transfer_value(998_900_000_000, spec) == "998.90 GB"


def test_tui_formats_request_counts_compactly() -> None:
    assert CftApp._format_request_count(1_234, width=6) == "1.23K"
    assert CftApp._format_request_count(99_890, width=7) == "99.89K"
    assert CftApp._format_request_count(1_234_000, width=6) == "1.23M"
    assert CftApp._format_request_count(987, width=4) == "987"
