from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from cft.aws.cloudfront import CloudFrontInventory, CloudFrontInventoryService
from cft.models.distribution import DistributionSummary

InventoryLoader = Callable[[], CloudFrontInventory]


class CftApp(App[None]):
    """Stage 1 CloudFront distribution browser."""

    TITLE = "cft"
    SUB_TITLE = "CloudFront distribution browser"
    BINDINGS = [("q", "quit", "Quit")]
    CSS_PATH = Path(__file__).with_name("cft.tcss")

    def __init__(
        self,
        *,
        profile_name: str | None = None,
        inventory_loader: InventoryLoader | None = None,
        now: Callable[[], datetime] = datetime.now,
        watch_css: bool = False,
    ) -> None:
        super().__init__(watch_css=watch_css)
        self.profile_name = profile_name
        self.inventory_loader = inventory_loader or CloudFrontInventoryService(
            profile_name=profile_name
        ).load
        self.now = now
        self.inventory: CloudFrontInventory | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="page"):
            with Vertical(id="summary"):
                yield self._summary_row("Date", self.now().strftime("%Y-%m-%d %H:%M:%S %Z"))
                yield self._summary_row("Profile", self.profile_name or "default")
                yield self._summary_row("Account", "-")
                yield self._summary_row("Download", "-")
                yield self._summary_row("Upload", "-")
                yield self._summary_row("Requests", "-")
                yield self._summary_row("CUR bucket", "-")
            yield Static("", id="status")
            yield DataTable(id="distributions", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#distributions", DataTable)
        table.add_columns("Dist", "Comment", "Type", "URL", "On", "Log", "UL/DL", "Req")
        try:
            self.inventory = self.inventory_loader()
        except Exception as error:  # pragma: no cover - exact boto3 errors vary by credential setup.
            self.query_one("#status", Static).update(f"AWS inventory unavailable: {error}")
            return

        self._update_summary(self.inventory)
        self._populate_table(table, self.inventory.distributions)

    @staticmethod
    def _summary_row(label: str, value: str) -> Horizontal:
        return Horizontal(
            Static(label, classes="label"),
            Static(value, id=f"summary-{label.lower().replace(' ', '-')}", classes="value"),
            classes="summary-row",
        )

    def _update_summary(self, inventory: CloudFrontInventory) -> None:
        self.query_one("#summary-profile", Static).update(inventory.profile_name)
        if inventory.identity is not None and inventory.identity.account_id:
            self.query_one("#summary-account", Static).update(inventory.identity.account_id)

    def _populate_table(
        self,
        table: DataTable[str],
        distributions: tuple[DistributionSummary, ...],
    ) -> None:
        if not distributions:
            self.query_one("#status", Static).update("No CloudFront distributions found.")
            return

        for distribution in distributions:
            table.add_row(
                distribution.distribution_id or "-",
                distribution.comment or "-",
                "-",
                distribution.domain_name or "-",
                self._enabled_marker(distribution),
                "-",
                "-/-",
                "-",
            )

    @staticmethod
    def _enabled_marker(distribution: DistributionSummary) -> str:
        if not distribution.enabled:
            return "Off"
        if distribution.status.lower() == "deployed":
            return "On"
        return distribution.status or "On"


def run_tui(profile_name: str | None = None, *, watch_css: bool = False) -> None:
    CftApp(profile_name=profile_name, watch_css=watch_css).run()
