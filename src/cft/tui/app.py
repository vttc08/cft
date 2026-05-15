from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual import work
from textual.widgets import (
    Button,
    DataTable,
    Digits,
    Footer,
    Header,
    Link,
    ProgressBar,
    Static,
)
from textual.widgets._progress_bar import Bar

from cft.aws.cloudwatch import CloudFrontUsageService
from cft.aws.cloudfront import CloudFrontInventory, CloudFrontInventoryService
from cft.aws.s3 import S3BucketDiscoveryService
from cft.config.paths import AppPaths, get_app_paths
from cft.config.settings import (
    display_data_export_prefix,
    load_app_settings,
    save_data_export_settings,
    settings_profile_name,
)
from cft.data_exports import BillingSnapshot, CurDataExportService
from cft.models.cache import SourceMetrics
from cft.models.distribution import DistributionSummary
from cft.tui.screens.cur_export_setup import (
    CurExportSetupResult,
    CurExportSetupScreen,
    CurExportStatus,
)
from cft.tui.screens.distribution_detail import DistributionDetailScreen

InventoryLoader = Callable[[], CloudFrontInventory]
UsageLoader = Callable[[CloudFrontInventory], dict[str, SourceMetrics]]
BillingLoader = Callable[[], BillingSnapshot]
T = TypeVar("T")

@dataclass(frozen=True)
class TableColumnSpec:
    key: str
    label: str
    min_width: int
    preferred_width: int
    max_width: int | None
    priority: int
    align: str = "left"
    gap_after: int = 1


@dataclass(frozen=True)
class ResolvedColumn:
    spec: TableColumnSpec
    visible_width: int

    @property
    def render_width(self) -> int:
        return self.visible_width + self.spec.gap_after


@dataclass(frozen=True)
class TransferFormatSpec:
    suffix: str
    decimals: int


@dataclass(frozen=True)
class SummaryPreviewData:
    profile_name: str
    account_id: str
    download_bytes: int | float | None
    upload_bytes: int | float | None
    cost: int | float | None
    requests: int | float | None
    last_updated: datetime | None = None


class SetupDataExportLink(Link):
    def action_open_link(self) -> None:
        app = self.app
        if isinstance(app, CftApp):
            app.action_setup_cur_export()


MOCK_SUMMARY_DATA = SummaryPreviewData(
    profile_name="dev",
    account_id="123456789012",
    download_bytes=None,
    upload_bytes=None,
    cost=None,
    requests=None,
)

class SummaryWidgetShowcase(Vertical):
    def __init__(
        self,
        *,
        profile_name: str,
        data: SummaryPreviewData = MOCK_SUMMARY_DATA,
        account_id: str | None = None,
        cur_export_status: CurExportStatus | None = None,
    ) -> None:
        super().__init__(id="summary-showcase")
        self.profile_name = profile_name
        self.data = data
        self.account_id = account_id or data.account_id
        self.cur_export_status = cur_export_status or CurExportStatus()
        self.now_value: datetime | None = None
        self.last_updated_value: datetime | None = data.last_updated

    def compose(self) -> ComposeResult:
        with Horizontal(id="summary-top-row"):
            with Vertical(id="summary-intro", classes="summary-panel"):
                yield Static("", classes="summary-note", id="summary-note")
                yield Static("", classes="summary-detail", id="summary-now")
                yield Static("", classes="summary-detail", id="summary-last-updated")
            with Vertical(id="summary-cur-export", classes="summary-panel"):
                yield Static("CUR Export", classes="summary-panel-title", id="summary-cur-export-title")
                yield Static("", classes="summary-note", id="summary-cur-export-bucket")
                yield Static("", classes="summary-detail", id="summary-cur-export-detail")
                yield SetupDataExportLink(
                    "Setup Data Export",
                    id="summary-cur-export-action",
                    classes="link-button",
                )

        with Horizontal(id="summary-metrics"):
            # capacities are configurable; defaults chosen for demo
            yield self._metric_card(
                "Download",
                self._format_summary_transfer(self.data.download_bytes),
                "summary-download-card",
                "summary-download-value",
                capacity_gb=1024,
            )
            yield self._metric_card(
                "Upload",
                self._format_summary_transfer(self.data.upload_bytes),
                "summary-upload-card",
                "summary-upload-value",
                capacity_gb=0.249,
            )
            yield self._metric_card(
                "Requests",
                CftApp._format_request_cell(self.data.requests, width=8),
                "summary-requests-card",
                "summary-requests-value",
                capacity_requests=10_000_000,
            )
            yield self._cost_metric_card(
                "Cost",
                self.data.cost,
                "summary-cost-card",
                "summary-cost-value",
            )

    def on_mount(self) -> None:
        self._refresh_summary_layout()
        # Metric bars use per-card IDs generated by _metric_card.
        download_bar = self.query_one("#summary-download-card-bar", ProgressBar)
        download_gb = float(self.data.download_bytes or 0) / float(BYTES_PER_GB)
        download_bar.update(total=download_bar.total or 1, progress=download_gb)

        upload_bar = self.query_one("#summary-upload-card-bar", ProgressBar)
        upload_gb = float(self.data.upload_bytes or 0) / float(BYTES_PER_GB)
        upload_bar.update(total=upload_bar.total or 1, progress=upload_gb)

        req_bar = self.query_one("#summary-requests-card-bar", ProgressBar)
        req_count = int(round(float(self.data.requests or 0)))
        req_bar.update(total=req_bar.total or 1, progress=req_count)

    def on_resize(self, event: events.Resize) -> None:
        self._refresh_summary_layout()

    def set_profile_account(
        self,
        *,
        profile_name: str,
        account_id: str | None,
        now: datetime,
    ) -> None:
        self.profile_name = profile_name
        self.account_id = account_id or "-"
        self.now_value = now
        self._refresh_summary_layout()

    def set_cur_export_status(self, status: CurExportStatus) -> None:
        self.cur_export_status = status
        self._refresh_summary_layout()

    def set_last_updated(self, last_updated: datetime | None) -> None:
        self.last_updated_value = last_updated
        self._refresh_summary_layout()

    def set_data(self, data: SummaryPreviewData) -> None:
        self.data = data
        self.last_updated_value = data.last_updated
        self.query_one("#summary-download-value", Static).update(
            self._format_summary_transfer(self.data.download_bytes)
        )
        self.query_one("#summary-upload-value", Static).update(
            self._format_summary_transfer(self.data.upload_bytes)
        )
        self.query_one("#summary-requests-value", Static).update(
            CftApp._format_request_cell(self.data.requests, width=8)
        )
        self._refresh_cost_value(self.data.cost)
        self.query_one("#summary-download-card-bar", ProgressBar).update(
            total=self.query_one("#summary-download-card-bar", ProgressBar).total or 1,
            progress=float(self.data.download_bytes or 0) / float(BYTES_PER_GB),
        )
        self.query_one("#summary-upload-card-bar", ProgressBar).update(
            total=self.query_one("#summary-upload-card-bar", ProgressBar).total or 1,
            progress=float(self.data.upload_bytes or 0) / float(BYTES_PER_GB),
        )
        self.query_one("#summary-requests-card-bar", ProgressBar).update(
            total=self.query_one("#summary-requests-card-bar", ProgressBar).total or 1,
            progress=int(round(float(self.data.requests or 0))),
        )
        self._refresh_summary_layout()

    def _refresh_summary_layout(self) -> None:
        intro_width = self._panel_content_width("#summary-intro")
        export_width = self._panel_content_width("#summary-cur-export")

        self.query_one("#summary-note", Static).update(
            self._format_profile_account_line(intro_width)
        )
        self.query_one("#summary-now", Static).update(
            self._format_timestamp_line("Now", self.now_value, intro_width)
        )
        self.query_one("#summary-last-updated", Static).update(
            self._format_timestamp_line("Last updated", self.last_updated_value, intro_width)
        )

        self.query_one("#summary-cur-export-bucket", Static).update(
            self._format_cur_export_bucket(export_width)
        )
        self.query_one("#summary-cur-export-detail", Static).update(
            self._format_cur_export_detail(export_width)
        )
        self.query_one("#summary-cur-export-action", SetupDataExportLink).update(
            "Edit Data Export" if self.cur_export_status.is_configured else "Setup Data Export"
        )

    def _panel_content_width(self, widget_id: str) -> int:
        try:
            width = self.query_one(widget_id).size.width
        except Exception:
            width = 0
        if width <= 0:
            width = max(0, self.size.width // 2)
        return max(0, width)

    def _format_profile_account_line(self, width: int) -> str:
        full = f"Profile {self.profile_name} · Account {self.account_id}"
        compact = f"{self.profile_name} · {self.account_id}"
        for candidate in (full, compact):
            if len(candidate) <= width:
                return candidate
        return CftApp._truncate(compact, width)

    def _format_timestamp_line(self, label: str, timestamp: datetime | None, width: int) -> str:
        if timestamp is None:
            return f"{label}: -"

        candidates = (
            f"{label}: {timestamp:%Y-%m-%d %H:%M:%S}",
            f"{timestamp:%m-%d %H:%M}",
            f"{timestamp:%H:%M:%S}",
        )
        for candidate in candidates:
            if len(candidate) <= width:
                return candidate
        return CftApp._truncate(candidates[-1], width)

    def _format_cur_export_bucket(self, width: int) -> str:
        bucket = self.cur_export_status.bucket if self.cur_export_status.is_configured else "Not configured"
        return bucket if len(bucket) <= width else CftApp._truncate(bucket, width)

    def _format_cur_export_detail(self, width: int) -> str:
        if not self.cur_export_status.is_configured:
            detail = "Path: / · Export: -"
            return detail if len(detail) <= width else CftApp._truncate(detail, width)

        full = (
            f"Path: {display_data_export_prefix(self.cur_export_status.prefix)}"
            f" · Export: {self.cur_export_status.export_name or '-'}"
        )
        if len(full) <= width:
            return full

        compact = " · ".join(
            [
                display_data_export_prefix(self.cur_export_status.prefix),
                self.cur_export_status.export_name or "-",
            ]
        )
        return compact if len(compact) <= width else CftApp._truncate(compact, width)

    @staticmethod
    def _metric_card(
        label: str,
        value: str,
        card_id: str,
        value_id: str,
        *,
        capacity_gb: float | None = None,
        capacity_requests: int | None = None,
    ) -> Vertical:
        # If a capacity in GB is provided, render a progress bar with that total.
        children: list[object] = [Static(label, classes="summary-card-label")]
        if capacity_gb is not None:
            # progress bar for sizes uses GB units
            children.append(
                ResponsiveProgressBar(
                    total=capacity_gb,
                    show_eta=False,
                    show_percentage=False,
                    id=f"{card_id}-bar",
                )
            )
            children.append(Static(value, id=value_id, classes="summary-card-value"))
        elif capacity_requests is not None:
            children.append(
                ResponsiveProgressBar(
                    total=capacity_requests,
                    show_eta=False,
                    show_percentage=False,
                    id=f"{card_id}-bar",
                )
            )
            children.append(Static(value, id=value_id, classes="summary-card-value"))
        else:
            children.append(Static(value, id=value_id, classes="summary-card-value"))

        return Vertical(*children, classes="summary-card panel", id=card_id)

    @staticmethod
    def _cost_metric_card(
        label: str,
        value: int | float | str | None,
        card_id: str,
        value_id: str,
    ) -> Vertical:
        return Vertical(
            Static(label, classes="summary-card-label"),
            Horizontal(
                Static("$", id="summary-cost-prefix", classes="summary-cost-prefix"),
                Digits(
                    SummaryWidgetShowcase._cost_digits_text(value),
                    id=value_id,
                    classes="summary-cost-digits",
                ),
                classes="summary-cost-display",
                id="summary-cost-display",
            ),
            classes="summary-card panel",
            id=card_id,
        )

    @staticmethod
    def _format_summary_transfer(value_bytes: int | float | None) -> str:
        if value_bytes is None:
            return "-"
        return CftApp._format_transfer_value(
            value_bytes,
            TransferFormatSpec(suffix=" GB", decimals=1),
        )

    @staticmethod
    def _cost_digits_text(value: int | float | str | None) -> str:
        if isinstance(value, str):
            return value.removeprefix("$")
        if value is None:
            return "-"
        return f"{value:.2f}"

    def _refresh_cost_value(self, value: int | float | None) -> None:
        self.query_one("#summary-cost-value", Digits).update(self._cost_digits_text(value))


class ClickableDataTable(DataTable[str]):
    async def _on_click(self, event: events.Click) -> None:
        old_cursor = self.cursor_coordinate
        meta = event.style.meta
        await super()._on_click(event)

        if self.cursor_type != "row" or not self.show_cursor:
            return
        if "row" not in meta or "column" not in meta:
            return
        if meta.get("out_of_bounds", False):
            return

        row_index = meta["row"]
        column_index = meta["column"]
        if self.show_header and row_index == -1:
            return
        if self.show_row_labels and column_index == -1:
            return

        if self.cursor_coordinate == old_cursor:
            return

        self._post_selected_message()


class ResponsiveProgressBar(ProgressBar):
    """ProgressBar variant that re-quantizes the bar fill after widget resizes."""

    def on_resize(self, event: events.Resize) -> None:
        if not self.show_bar:
            return
        try:
            bar = self.query_one("#bar", Bar)
        except Exception:
            return
        bar.percentage = self.percentage
        bar.gradient = self.gradient
        bar.refresh()


TABLE_COLUMNS: tuple[TableColumnSpec, ...] = (
    TableColumnSpec("dist", "ID", min_width=5, preferred_width=6, max_width=14, priority=0),
    TableColumnSpec("comment", "Comment", min_width=7, preferred_width=16, max_width=32, priority=1),
    TableColumnSpec("type", "Type", min_width=4, preferred_width=4, max_width=4, priority=0),
    TableColumnSpec("url", "URL", min_width=6, preferred_width=12, max_width=16, priority=2),
    TableColumnSpec("on", "On", min_width=2, preferred_width=2, max_width=2, priority=0, align="center"),
    TableColumnSpec("log", "Log", min_width=3, preferred_width=3, max_width=3, priority=0, align="center"),
    TableColumnSpec("down", "Down", min_width=5, preferred_width=9, max_width=9, priority=0, align="right"),
    TableColumnSpec("up", "Up", min_width=5, preferred_width=9, max_width=9, priority=0, align="right"),
    TableColumnSpec("req", "Req", min_width=5, preferred_width=6, max_width=6, priority=0, align="right", gap_after=0),
)

TABLE_TYPE_PLACEHOLDERS: tuple[str, ...] = ("Free", "PAYG", "-")
BYTES_PER_GB = Decimal("1000000000")
TRANSFER_PLACEHOLDERS: tuple[tuple[int, int], ...] = (
    (1_234_000_000, 1_200_000_000),
    (99_890_000_000, 1_200_000_000),
    (998_900_000_000, 1_234_000_000),
)
TRANSFER_FORMAT_SAMPLES: tuple[int, ...] = (
    1_234_000_000,
    99_890_000_000,
    998_900_000_000,
)

CFT_AWS_THEME = Theme(
    name="cft-aws",
    primary="#FF9900",     # AWS Orange
    secondary="#8C4FFF",   # CloudFront Purple
    accent="#FF9900",      # AWS Orange
    foreground="#F2F3F3",  # AWS Off-white
    background="#171A1F",  # AWS-neutral dark background
    surface="#1F2329",     # Neutral charcoal
    panel="#252B31",       # Dark grey
    success="#44B035",     # AWS Green
    warning="#FF9900",     # AWS Orange
    error="#E7157B",       # AWS Red/Pink
    dark=True,
    variables={
        "block-cursor-text-style": "none",
        "footer-key-foreground": "#B2B2B2",
        "input-selection-background": "#FF990033",
    },
)


class CftApp(App[None]):
    """Stage 1 CloudFront distribution browser."""

    TITLE = "cft"
    SUB_TITLE = "CloudFront distribution browser"
    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("b", "setup_cur_export", "CUR Export"),
        ("q", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]
    CSS_PATH = Path(__file__).with_name("cft.tcss")

    def __init__(
        self,
        *,
        profile_name: str | None = None,
        inventory_loader: InventoryLoader | None = None,
        usage_loader: UsageLoader | None = None,
        bucket_loader: Callable[[], tuple[str, ...]] | None = None,
        billing_loader: BillingLoader | None = None,
        paths: AppPaths | None = None,
        now: Callable[[], datetime] = datetime.now,
        watch_css: bool = False,
    ) -> None:
        super().__init__(watch_css=watch_css)
        self.register_theme(CFT_AWS_THEME)
        self.theme = CFT_AWS_THEME.name
        self.profile_name = profile_name
        self.paths = paths or get_app_paths()
        self.settings_profile_name = settings_profile_name(profile_name)
        self.settings = load_app_settings(
            self.paths,
            profile_name=self.settings_profile_name,
        )
        self._inventory_service = CloudFrontInventoryService(
            profile_name=profile_name,
            paths=self.paths,
        )
        self._usage_service = CloudFrontUsageService(
            profile_name=profile_name,
            paths=self.paths,
        )
        self._bucket_service = S3BucketDiscoveryService(
            profile_name=profile_name,
            paths=self.paths,
        )
        self._billing_service = CurDataExportService(
            profile_name=profile_name,
            paths=self.paths,
        )
        self._inventory_loader_is_default = inventory_loader is None
        self._usage_loader_is_default = usage_loader is None
        self._bucket_loader_is_default = bucket_loader is None
        self._billing_loader_is_default = billing_loader is None
        self.inventory_loader = inventory_loader or self._inventory_service.load
        self.usage_loader = usage_loader or self._default_usage_loader
        self.bucket_loader = bucket_loader or self._bucket_service.list_bucket_names
        self.billing_loader = billing_loader or self._billing_service.load
        self.now = now
        self.inventory: CloudFrontInventory | None = None
        self.usage_by_distribution: dict[str, SourceMetrics] = {}
        self.billing_snapshot = BillingSnapshot(
            profile_name=self.settings_profile_name,
            configured=False,
            message="Setup required",
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="page"):
            with Vertical(id="loading-panel", classes="panel"):
                yield Static("Loading CloudFront data...", id="loading-title")
                yield ProgressBar(id="loading-progress")
                yield Static("Preparing AWS session...", id="loading-status")
            with VerticalScroll(id="dashboard-scroll", classes="hidden"):
                yield SummaryWidgetShowcase(
                    profile_name=self.settings_profile_name,
                    cur_export_status=self._cur_export_status(),
                )
                with Vertical(id="table-shell"):
                    with Horizontal(id="table-heading"):
                        yield Static("Distributions", id="table-title")
                        yield Static(f"{self.now():%B %Y}", id="table-subtitle")
                    yield Static("", id="status")
                    yield ClickableDataTable(
                        id="distributions",
                        cursor_type="row",
                        zebra_stripes=True,
                        cell_padding=0,
                    )
        yield Footer()

    @work(exclusive=True)
    async def on_mount(self) -> None:
        await self._load_data(refresh=False)

    @work(exclusive=True)
    async def action_refresh(self) -> None:
        await self._load_data(refresh=True)

    def action_setup_cur_export(self) -> None:
        self._open_cur_export_setup()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "summary-cur-export-action":
            self._open_cur_export_setup()
            event.stop()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        distribution = self._distribution_for_key(event.row_key.value)
        if distribution is None:
            self.query_one("#status", Static).update(f"Selected distribution: {event.row_key.value}")
            return

        usage = self.usage_by_distribution.get(distribution.distribution_id, SourceMetrics())
        comment = distribution.comment or "-"
        self.query_one("#status", Static).update(
            f"Opened distribution {distribution.distribution_id}: {comment}"
        )
        self.push_screen(
            DistributionDetailScreen(
                distribution=distribution,
                usage=usage,
            )
        )

    def _check_resize(self) -> None:
        super()._check_resize()
        if self.inventory is not None:
            self._refresh_distribution_table()

    async def _load_data(self, *, refresh: bool) -> None:
        self._set_loading_state(True, "Loading CloudFront inventory...")
        try:
            self.inventory = await asyncio.to_thread(self._load_inventory, refresh=refresh)
        except Exception as error:  # pragma: no cover - exact boto3 errors vary by credential setup.
            self.query_one("#status", Static).update(f"AWS inventory unavailable: {error}")
            self._set_loading_state(False, f"AWS inventory unavailable: {error}")
            return

        try:
            self._set_loading_state(True, "Loading CloudWatch usage...")
            self.usage_by_distribution = await asyncio.to_thread(
                self._load_usage,
                self.inventory,
                refresh=refresh,
            )
        except Exception as error:  # pragma: no cover - exact boto3 errors vary by credential setup.
            self.usage_by_distribution = {}
            self.query_one("#status", Static).update(f"CloudWatch usage unavailable: {error}")
            self._set_loading_state(False, f"CloudWatch usage unavailable: {error}")
            return

        try:
            self._set_loading_state(True, "Loading CUR billing summary...")
            self.billing_snapshot = await asyncio.to_thread(
                self._load_billing,
                refresh=refresh,
            )
        except Exception as error:  # pragma: no cover - exact boto3/duckdb errors vary.
            self.billing_snapshot = BillingSnapshot(
                profile_name=self.settings_profile_name,
                configured=bool(
                    self.settings.data_export.bucket and self.settings.data_export.export_name
                ),
                message=f"Billing unavailable: {error}",
            )
            self.query_one("#status", Static).update(f"CUR billing unavailable: {error}")

        self._refresh_distribution_table()
        self._refresh_summary()
        self._set_loading_state(False, f"Loaded CloudFront data at {self.now():%H:%M:%S}")
        if refresh:
            refreshed_at = self.now().strftime("%H:%M:%S")
            message = f"Refreshed CloudWatch usage at {refreshed_at}"
            self.query_one("#status", Static).update(message)
            self.notify(message, title="cft refresh", severity="information", timeout=2.5)

    def _load_inventory(self, *, refresh: bool) -> CloudFrontInventory:
        if self._inventory_loader_is_default:
            return self._inventory_service.load(refresh=refresh)
        return self.inventory_loader()

    def _load_usage(
        self,
        inventory: CloudFrontInventory,
        *,
        refresh: bool,
    ) -> dict[str, SourceMetrics]:
        if self._usage_loader_is_default:
            snapshot = self._usage_service.load(inventory, refresh=refresh)
            return snapshot.usage_by_distribution
        return self.usage_loader(inventory)

    def _set_loading_state(self, loading: bool, message: str) -> None:
        loading_panel = self.query_one("#loading-panel", Vertical)
        dashboard = self.query_one("#dashboard-scroll", VerticalScroll)
        status = self.query_one("#loading-status", Static)
        status.update(message)
        if loading:
            loading_panel.remove_class("hidden")
            dashboard.add_class("hidden")
            self.query_one("#loading-progress", ProgressBar).update(total=None, progress=0)
        else:
            loading_panel.add_class("hidden")
            dashboard.remove_class("hidden")

    def _refresh_summary(self) -> None:
        summary = self.query_one("#summary-showcase", SummaryWidgetShowcase)
        account_id = self.inventory.identity.account_id if self.inventory and self.inventory.identity else None
        summary.set_data(
            SummaryPreviewData(
                profile_name=self.inventory.profile_name if self.inventory else self.settings_profile_name,
                account_id=account_id or "-",
                download_bytes=self.billing_snapshot.download_bytes,
                upload_bytes=self.billing_snapshot.upload_bytes,
                cost=self.billing_snapshot.cost,
                requests=self.billing_snapshot.requests,
                last_updated=self.billing_snapshot.data_end or self.billing_snapshot.last_updated,
            )
        )
        summary.set_profile_account(
            profile_name=self.inventory.profile_name if self.inventory else self.settings_profile_name,
            account_id=account_id,
            now=self.now(),
        )
        summary.set_cur_export_status(self._cur_export_status())
        summary.set_last_updated(self.billing_snapshot.data_end or self.billing_snapshot.last_updated)

    def _cur_export_status(self) -> CurExportStatus:
        return CurExportStatus(
            bucket=self.settings.data_export.bucket,
            prefix=self.settings.data_export.prefix,
            export_name=self.settings.data_export.export_name,
        )

    def _open_cur_export_setup(self) -> None:
        error_message = None
        try:
            bucket_names = self._load_bucket_names()
        except Exception as error:  # pragma: no cover - boto3 exception shapes vary.
            bucket_names = ()
            error_message = f"Bucket discovery failed: {error}"

        self.push_screen(
            CurExportSetupScreen(
                profile_name=self.settings_profile_name,
                bucket_names=bucket_names,
                initial_status=self._cur_export_status(),
                error_message=error_message,
            ),
            self._handle_cur_export_setup_result,
        )

    def _handle_cur_export_setup_result(
        self,
        result: CurExportSetupResult | None,
    ) -> None:
        if result is None:
            return

        save_data_export_settings(
            paths=self.paths,
            profile_name=self.settings_profile_name,
            bucket=result.bucket,
            prefix=result.prefix,
            export_name=result.export_name,
        )
        self.settings = load_app_settings(
            self.paths,
            profile_name=self.settings_profile_name,
            create=False,
        )
        self._refresh_summary()
        self.query_one("#status", Static).update(
            f"Linked CUR export bucket {result.bucket} for profile {self.settings_profile_name}."
        )

    def _load_bucket_names(self) -> tuple[str, ...]:
        if self._bucket_loader_is_default:
            return self._bucket_service.list_bucket_names()
        return self.bucket_loader()

    def _load_billing(self, *, refresh: bool) -> BillingSnapshot:
        if self._billing_loader_is_default:
            return self._billing_service.load(refresh=refresh)
        return self.billing_loader()

    def _refresh_distribution_table(self) -> None:
        if self.inventory is None:
            return

        table = self.query_one("#distributions", DataTable)
        columns = self._resolve_columns(self._available_table_width())
        table.clear(columns=True)
        for column in columns:
            table.add_column(
                self._format_cell(
                    column.spec.label,
                    width=column.visible_width,
                    align=column.spec.align,
                    gap_after=column.spec.gap_after,
                ),
                key=column.spec.key,
                width=column.render_width,
            )
        self._populate_rows(table, self.inventory.distributions, columns)

    def _distribution_for_key(self, key: str) -> DistributionSummary | None:
        if self.inventory is None:
            return None

        for distribution in self.inventory.distributions:
            if distribution.distribution_id == key:
                return distribution
        return None

    def _populate_rows(
        self,
        table: DataTable[str],
        distributions: tuple[DistributionSummary, ...],
        columns: tuple[ResolvedColumn, ...],
    ) -> None:
        if not distributions:
            self.query_one("#status", Static).update("No CloudFront distributions found.")
            return

        self.query_one("#status", Static).update("")
        column_map = {column.spec.key: column for column in columns}
        transfer_spec = self._resolve_transfer_format(
            TRANSFER_FORMAT_SAMPLES,
            width=min(
                column_map["down"].visible_width,
                column_map["up"].visible_width,
            ),
        )
        for index, distribution in enumerate(distributions):
            usage = self.usage_by_distribution.get(distribution.distribution_id, SourceMetrics())
            table.add_row(
                self._render_column_value(
                    column_map["dist"], distribution.distribution_id or "-"
                ),
                self._render_column_value(
                    column_map["comment"], distribution.comment or "-"
                ),
                self._render_column_value(
                    column_map["type"], self._demo_value(TABLE_TYPE_PLACEHOLDERS, index)
                ),
                self._render_column_value(
                    column_map["url"], distribution.domain_name or "-"
                ),
                self._render_column_value(
                    column_map["on"], self._distribution_status_marker(distribution)
                ),
                self._render_column_value(column_map["log"], self._log_status_marker()),
                self._render_column_value(
                    column_map["down"],
                    self._format_transfer_cell(usage.download, transfer_spec),
                ),
                self._render_column_value(
                    column_map["up"],
                    self._format_transfer_cell(usage.upload, transfer_spec),
                ),
                self._render_column_value(
                    column_map["req"],
                    self._format_request_cell(
                        usage.requests,
                        width=column_map["req"].visible_width,
                    ),
                ),
                key=distribution.distribution_id,
            )

    @staticmethod
    def _distribution_status_marker(distribution: DistributionSummary) -> Text:
        if not distribution.enabled:
            return Text("○", style="#E7157B")
        if distribution.status.lower() == "deployed":
            return Text("●", style="green")
        return Text("◐", style="color(214)")

    @staticmethod
    def _log_status_marker() -> Text:
        return Text("-", style="color(214)")

    @staticmethod
    def _truncate(value: str, width: int) -> str:
        if width <= 0:
            return ""
        if len(value) <= width:
            return value
        if width <= 3:
            return value[:width]
        return f"{value[: width - 2]}.."

    @staticmethod
    def _resolve_columns(available_width: int) -> tuple[ResolvedColumn, ...]:
        visible_widths = {column.key: column.min_width for column in TABLE_COLUMNS}
        separator_width = sum(column.gap_after for column in TABLE_COLUMNS)
        remaining = max(0, available_width - separator_width - sum(visible_widths.values()))
        expand_order = tuple(sorted(TABLE_COLUMNS, key=lambda column: column.priority))

        remaining = CftApp._grow_columns(
            visible_widths,
            expand_order,
            remaining,
            target=lambda column: min(column.preferred_width, column.max_width or column.preferred_width),
        )
        CftApp._grow_columns(
            visible_widths,
            expand_order,
            remaining,
            target=lambda column: column.max_width,
        )

        return tuple(
            ResolvedColumn(spec=column, visible_width=visible_widths[column.key])
            for column in TABLE_COLUMNS
        )

    @staticmethod
    def _grow_columns(
        visible_widths: dict[str, int],
        columns: tuple[TableColumnSpec, ...],
        remaining: int,
        target: Callable[[TableColumnSpec], int | None],
    ) -> int:
        while remaining > 0:
            allocated = False
            for column in columns:
                target_width = target(column)
                if target_width is None:
                    target_width = visible_widths[column.key] + remaining
                if visible_widths[column.key] >= target_width:
                    continue
                visible_widths[column.key] += 1
                remaining -= 1
                allocated = True
                if remaining == 0:
                    break
            if not allocated:
                break
        return remaining

    @staticmethod
    def _render_column_value(column: ResolvedColumn, value: str | Text) -> Text:
        return CftApp._format_cell(
            value,
            width=column.visible_width,
            align=column.spec.align,
            gap_after=column.spec.gap_after,
        )

    @staticmethod
    def _format_cell(value: str | Text, *, width: int, align: str, gap_after: int) -> Text:
        if isinstance(value, Text):
            rendered = value.copy()
            if rendered.cell_len > width:
                rendered.truncate(width, overflow="ellipsis")
            padding = max(0, width - rendered.cell_len)
            if align == "right":
                rendered.pad_left(padding)
            elif align == "center":
                left_padding = padding // 2
                rendered.pad_left(left_padding)
                rendered.pad_right(padding - left_padding)
            else:
                rendered.pad_right(padding)
            if gap_after:
                rendered.append(" " * gap_after)
            return rendered

        visible_value = CftApp._truncate(value, width)
        if align == "right":
            rendered = visible_value.rjust(width)
        elif align == "center":
            rendered = visible_value.center(width)
        else:
            rendered = visible_value.ljust(width)
        return Text(f"{rendered}{' ' * gap_after}")

    def _available_table_width(self) -> int:
        return max(0, self.size.width - 6)

    @staticmethod
    def _demo_value(values: tuple[T, ...], index: int) -> T:
        return values[index % len(values)]

    @staticmethod
    def _resolve_transfer_format(values: tuple[int, ...], *, width: int) -> TransferFormatSpec:
        largest = max(values)
        for suffix in (" GB", " G"):
            for decimals in (2, 1, 0):
                rendered = CftApp._format_transfer_value(
                    largest,
                    TransferFormatSpec(suffix=suffix, decimals=decimals),
                )
                if len(rendered) <= width:
                    return TransferFormatSpec(suffix=suffix, decimals=decimals)

        return TransferFormatSpec(suffix=" G", decimals=0)

    @staticmethod
    def _format_transfer_value(value_bytes: int | float, spec: TransferFormatSpec) -> str:
        value_gb = Decimal(str(value_bytes)) / BYTES_PER_GB
        quantized = value_gb.quantize(
            Decimal(1).scaleb(-spec.decimals),
            rounding=ROUND_HALF_UP,
        )
        if spec.decimals == 0:
            number = f"{quantized:.0f}"
        else:
            number = f"{quantized:.{spec.decimals}f}"
        return f"{number}{spec.suffix}"

    @staticmethod
    def _format_transfer_cell(
        value_bytes: int | float | None,
        spec: TransferFormatSpec,
    ) -> str:
        if value_bytes is None:
            return "-"
        return CftApp._format_transfer_value(value_bytes, spec)

    @staticmethod
    def _format_request_count(value: int | float, *, width: int) -> str:
        count = Decimal(str(value))
        if count < 1000:
            rounded = count.to_integral_value(rounding=ROUND_HALF_UP)
            return str(int(rounded))

        for suffix, divisor in (("K", Decimal("1000")), ("M", Decimal("1000000")), ("B", Decimal("1000000000"))):
            if count < divisor * 1000:
                scaled = count / divisor
                preferred_decimals = 2
                for decimals in range(preferred_decimals, -1, -1):
                    rendered = CftApp._format_compact_number(scaled, decimals, suffix)
                    if len(rendered) <= width:
                        return rendered
                return CftApp._format_compact_number(scaled, 0, suffix)

        scaled = count / Decimal("1000000000000")
        preferred_decimals = 2
        for decimals in range(preferred_decimals, -1, -1):
            rendered = CftApp._format_compact_number(scaled, decimals, "T")
            if len(rendered) <= width:
                return rendered
        return CftApp._format_compact_number(scaled, 0, "T")

    @staticmethod
    def _format_request_cell(value: int | float | None, *, width: int) -> str:
        if value is None:
            return "-"
        return CftApp._format_request_count(value, width=width)

    @staticmethod
    def _format_compact_number(value: Decimal, decimals: int, suffix: str) -> str:
        quantized = value.quantize(
            Decimal(1).scaleb(-decimals),
            rounding=ROUND_HALF_UP,
        )
        if decimals == 0:
            number = f"{quantized:.0f}"
        else:
            number = f"{quantized:.{decimals}f}"
        return f"{number}{suffix}"

    def _default_usage_loader(self, inventory: CloudFrontInventory) -> dict[str, SourceMetrics]:
        snapshot = CloudFrontUsageService(profile_name=inventory.profile_name).load(inventory)
        return snapshot.usage_by_distribution

def run_tui(profile_name: str | None = None, *, watch_css: bool = False) -> None:
    CftApp(profile_name=profile_name, watch_css=watch_css).run()
