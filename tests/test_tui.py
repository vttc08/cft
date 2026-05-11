from datetime import datetime
import asyncio

from cft.aws.cloudfront import AccountIdentity, CloudFrontInventory
from cft.models.distribution import DistributionSummary
from cft.tui.app import CftApp


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

        assert app.query_one("#summary-date").content == "2026-05-11 09:30:00 "
        assert app.query_one("#summary-profile").content == "dev"
        assert app.query_one("#summary-account").content == "123456789012"
        assert app.query_one("#summary-download").content == "-"

        table = app.query_one("#distributions")
        assert table.row_count == 1
