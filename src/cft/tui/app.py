from __future__ import annotations

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
from textual.widgets import DataTable, Digits, Footer, Header, ProgressBar, Sparkline, Static

from cft.aws.cloudfront import CloudFrontInventory, CloudFrontInventoryService
from cft.models.distribution import DistributionSummary

InventoryLoader = Callable[[], CloudFrontInventory]
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
    download: str
    upload: str
    cost: str
    requests: str
    download_trend: tuple[int, ...]
    upload_trend: tuple[int, ...]
    budget_total: float
    budget_progress: float
    budget_label: str


MOCK_SUMMARY_DATA = SummaryPreviewData(
    profile_name="dev",
    account_id="123456789012",
    download="128.4 GB",
    upload="6.8 GB",
    cost="$8.42",
    requests="1.24M",
    download_trend=(54, 58, 60, 63, 72, 74, 79, 84, 88, 92, 95, 98),
    upload_trend=(6, 6, 5, 7, 7, 8, 8, 7, 9, 9, 10, 11),
    budget_total=50.0,
    budget_progress=38.42,
    budget_label="$38.42 of $50.00",
)


class SummaryWidgetShowcase(Vertical):
    def __init__(self, *, profile_name: str, data: SummaryPreviewData = MOCK_SUMMARY_DATA) -> None:
        super().__init__(id="summary-showcase")
        self.profile_name = profile_name
        self.data = data

    def compose(self) -> ComposeResult:
        with Vertical(id="summary-intro", classes="summary-panel"):
            yield Static(
                f"Profile {self.profile_name} · Account {self.data.account_id}",
                classes="summary-note",
                id="summary-note",
            )

        with Horizontal(id="summary-metrics"):
            # capacities are configurable; defaults chosen for demo
            yield self._metric_card(
                "Download",
                self.data.download,
                "summary-download-card",
                "summary-download-value",
                capacity_gb=1024,
            )
            yield self._metric_card(
                "Upload",
                self.data.upload,
                "summary-upload-card",
                "summary-upload-value",
                capacity_gb=1,
            )
            yield self._metric_card(
                "Requests",
                self.data.requests,
                "summary-requests-card",
                "summary-requests-value",
                capacity_requests=20_000_000,
            )
            yield self._cost_metric_card(
                "Cost",
                self.data.cost,
                "summary-cost-card",
                "summary-cost-value",
            )

        with Horizontal(id="summary-visuals"):
            with Vertical(id="summary-trends", classes="summary-panel"):
                yield Static("Traffic trend", classes="summary-panel-title")
                yield Sparkline(
                    self.data.download_trend,
                    summary_function=max,
                    min_color="#8C4FFF",
                    max_color="#FF9900",
                    id="summary-download-trend",
                )
                yield Static("Download trend preview", classes="summary-detail")
                yield Static("Upload trend preview", classes="summary-detail")
                yield Sparkline(
                    self.data.upload_trend,
                    summary_function=max,
                    min_color="#44B035",
                    max_color="#FF9900",
                    id="summary-upload-trend",
                )
            with Vertical(id="summary-budget", classes="summary-panel"):
                yield Static("Budget usage", classes="summary-panel-title")
                yield ProgressBar(
                    total=self.data.budget_total,
                    show_eta=False,
                    id="summary-budget-bar",
                )
                yield Static(self.data.budget_label, classes="summary-detail", id="summary-budget-label")

    def on_mount(self) -> None:
        self.query_one("#summary-budget-bar", ProgressBar).update(
            total=self.data.budget_total,
            progress=self.data.budget_progress,
        )
        # Metric bars use per-card IDs generated by _metric_card.
        download_bar = self.query_one("#summary-download-card-bar", ProgressBar)
        download_gb = CftApp._parse_size_gb(self.data.download)
        download_bar.update(total=download_bar.total or 1, progress=download_gb)

        upload_bar = self.query_one("#summary-upload-card-bar", ProgressBar)
        upload_gb = CftApp._parse_size_gb(self.data.upload)
        upload_bar.update(total=upload_bar.total or 1, progress=upload_gb)

        req_bar = self.query_one("#summary-requests-card-bar", ProgressBar)
        req_count = CftApp._parse_requests(self.data.requests)
        req_bar.update(total=req_bar.total or 1, progress=req_count)

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
            children.append(ProgressBar(total=capacity_gb, show_eta=False, id=f"{card_id}-bar"))
            children.append(Static(value, id=value_id, classes="summary-card-value"))
        elif capacity_requests is not None:
            children.append(ProgressBar(total=capacity_requests, show_eta=False, id=f"{card_id}-bar"))
            children.append(Static(value, id=value_id, classes="summary-card-value"))
        else:
            children.append(Static(value, id=value_id, classes="summary-card-value"))

        return Vertical(*children, classes="summary-card panel", id=card_id)

    @staticmethod
    def _cost_metric_card(label: str, value: str, card_id: str, value_id: str) -> Vertical:
        return Vertical(
            Static(label, classes="summary-card-label"),
            Horizontal(
                Static("$", id="summary-cost-prefix", classes="summary-cost-prefix"),
                Digits(SummaryWidgetShowcase._cost_digits_text(value), id=value_id, classes="summary-cost-digits"),
                classes="summary-cost-display",
            ),
            classes="summary-card panel",
            id=card_id,
        )

    @staticmethod
    def _cost_digits_text(value: str) -> str:
        return value.removeprefix("$")


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


TABLE_COLUMNS: tuple[TableColumnSpec, ...] = (
    TableColumnSpec("dist", "ID", min_width=4, preferred_width=6, max_width=6, priority=0),
    TableColumnSpec("comment", "Comment", min_width=7, preferred_width=16, max_width=None, priority=1),
    TableColumnSpec("type", "Type", min_width=4, preferred_width=4, max_width=4, priority=0),
    TableColumnSpec("url", "URL", min_width=6, preferred_width=12, max_width=16, priority=2),
    TableColumnSpec("on", "On", min_width=2, preferred_width=2, max_width=2, priority=0, align="center"),
    TableColumnSpec("log", "Log", min_width=3, preferred_width=3, max_width=3, priority=0, align="center"),
    TableColumnSpec("down", "Down", min_width=5, preferred_width=9, max_width=9, priority=0, align="right"),
    TableColumnSpec("up", "Up", min_width=5, preferred_width=9, max_width=9, priority=0, align="right"),
    TableColumnSpec("req", "Req", min_width=5, preferred_width=6, max_width=6, priority=0, align="right", gap_after=0),
)

TABLE_TYPE_PLACEHOLDERS: tuple[str, ...] = ("Free", "PAYG", "?")
BYTES_PER_GB = Decimal("1000000000")
TRANSFER_PLACEHOLDERS: tuple[tuple[int, int], ...] = (
    (1_234_000_000, 1_200_000_000),
    (99_890_000_000, 1_200_000_000),
    (998_900_000_000, 1_234_000_000),
)
REQUEST_PLACEHOLDERS: tuple[int, ...] = (1_234, 99_890, 1_234_000)

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
    BINDINGS = [("q", "quit", "Quit"), ("ctrl+q", "quit", "Quit"), ("ctrl+c", "quit", "Quit")]
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
        self.register_theme(CFT_AWS_THEME)
        self.theme = CFT_AWS_THEME.name
        self.profile_name = profile_name
        self.inventory_loader = inventory_loader or CloudFrontInventoryService(
            profile_name=profile_name
        ).load
        self.now = now
        self.inventory: CloudFrontInventory | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="page"):
            with VerticalScroll(id="dashboard-scroll"):
                yield SummaryWidgetShowcase(profile_name=self.profile_name or "default")
                with Vertical(id="table-shell"):
                    with Horizontal(id="table-heading"):
                        yield Static("Distributions", id="table-title")
                        yield Static("Current month operational view", id="table-subtitle")
                    yield Static("", id="status")
                    yield ClickableDataTable(
                        id="distributions",
                        cursor_type="row",
                        zebra_stripes=True,
                        cell_padding=0,
                    )
        yield Footer()

    def on_mount(self) -> None:
        try:
            self.inventory = self.inventory_loader()
        except Exception as error:  # pragma: no cover - exact boto3 errors vary by credential setup.
            self.query_one("#status", Static).update(f"AWS inventory unavailable: {error}")
            return

        self._refresh_distribution_table()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        distribution = self._distribution_for_key(event.row_key.value)
        if distribution is None:
            self.query_one("#status", Static).update(f"Selected distribution: {event.row_key.value}")
            return

        comment = distribution.comment or "-"
        self.query_one("#status", Static).update(
            f"Selected distribution {distribution.distribution_id}: {comment}"
        )

    def _check_resize(self) -> None:
        super()._check_resize()
        if self.inventory is not None:
            self._refresh_distribution_table()

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
            TRANSFER_PLACEHOLDERS,
            width=min(
                column_map["down"].visible_width,
                column_map["up"].visible_width,
            ),
        )
        for index, distribution in enumerate(distributions):
            down_bytes, up_bytes = self._demo_value(TRANSFER_PLACEHOLDERS, index)
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
                    column_map["down"], self._format_transfer_value(down_bytes, transfer_spec)
                ),
                self._render_column_value(
                    column_map["up"], self._format_transfer_value(up_bytes, transfer_spec)
                ),
                self._render_column_value(
                    column_map["req"],
                    self._format_request_count(
                        self._demo_value(REQUEST_PLACEHOLDERS, index),
                        width=column_map["req"].visible_width,
                    ),
                ),
                key=distribution.distribution_id,
            )

    @staticmethod
    def _distribution_status_marker(distribution: DistributionSummary) -> Text:
        if not distribution.enabled:
            return Text("○", style="bright_black")
        if distribution.status.lower() == "deployed":
            return Text("●", style="green")
        return Text("◐", style="yellow")

    @staticmethod
    def _log_status_marker() -> Text:
        return Text("·", style="yellow")

    @staticmethod
    def _truncate(value: str, width: int) -> str:
        if width <= 0:
            return ""
        if len(value) <= width:
            return value
        if width <= 3:
            return value[:width]
        return f"{value[: width - 3]}..."

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
    def _resolve_transfer_format(
        values: tuple[tuple[int, int], ...],
        *,
        width: int,
    ) -> TransferFormatSpec:
        largest = max(max(pair) for pair in values)
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
    def _format_transfer_value(value_bytes: int, spec: TransferFormatSpec) -> str:
        value_gb = Decimal(value_bytes) / BYTES_PER_GB
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
    def _format_request_count(value: int, *, width: int) -> str:
        count = Decimal(value)
        if value < 1000:
            return str(value)

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

    @staticmethod
    def _parse_size_gb(value: str) -> float:
        # accepts strings like '128.4 GB', '6.8 GB', '249 MB'
        try:
            parts = value.strip().split()
            if not parts:
                return 0.0
            number = float(parts[0].replace(',', ''))
            unit = parts[1].upper() if len(parts) > 1 else "B"
            if unit.startswith("GB") or unit == "G":
                return number
            if unit.startswith("MB"):
                return number / 1000.0
            if unit.startswith("B"):
                # interpret as bytes
                return number / float(BYTES_PER_GB)
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _parse_requests(value: str) -> int:
        # accepts compact forms like '1.24M', '1234', '10M'
        try:
            s = value.strip().upper()
            if s.endswith("M"):
                return int(float(s[:-1]) * 1_000_000)
            if s.endswith("K"):
                return int(float(s[:-1]) * 1000)
            if s.endswith("B"):
                return int(float(s[:-1]) * 1_000_000_000)
            return int(float(s.replace(',', '')))
        except Exception:
            return 0


def run_tui(profile_name: str | None = None, *, watch_css: bool = False) -> None:
    CftApp(profile_name=profile_name, watch_css=watch_css).run()
