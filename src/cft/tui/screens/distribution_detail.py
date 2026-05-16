from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from textual.app import ComposeResult
from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, Static

from cft.models.cache import SourceMetrics
from cft.models.cache import StandardLogDeliveryRecord
from cft.models.cache import normalize_distribution_type
from cft.models.distribution import DistributionSummary

BYTES_PER_GB = Decimal("1000000000")


class DistributionDetailScreen(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
    ]

    def __init__(
        self,
        *,
        distribution: DistributionSummary,
        distribution_type: str,
        usage: SourceMetrics,
        standard_log_deliveries: tuple[StandardLogDeliveryRecord, ...] = (),
    ) -> None:
        super().__init__()
        self.distribution = distribution
        self.distribution_type = normalize_distribution_type(distribution_type)
        self.usage = usage
        self.standard_log_deliveries = standard_log_deliveries

    def compose(self) -> ComposeResult:
        title = self.distribution.comment or self.distribution.distribution_id or "Distribution"

        with Container(id="distribution-detail-modal"):
            with Vertical(id="distribution-detail-dialog", classes="panel"):
                yield Static(title, id="distribution-detail-title")
                yield Static(
                    self.distribution.distribution_id or "-",
                    id="distribution-detail-subtitle",
                )

                with VerticalScroll(id="distribution-detail-content"):
                    yield Static("Attributes", classes="distribution-detail-section-title")
                    yield self._row("ID", self.distribution.distribution_id, "distribution-detail-id")
                    yield self._row_type(
                        "Plan type",
                        self.distribution_type,
                        "distribution-detail-type",
                    )
                    yield self._row("ARN", self.distribution.arn, "distribution-detail-arn")
                    yield self._row("Domain", self.distribution.domain_name, "distribution-detail-domain")
                    yield self._row("Status", self.distribution.status, "distribution-detail-status")
                    yield self._row(
                        "Enabled",
                        "Yes" if self.distribution.enabled else "No",
                        "distribution-detail-enabled",
                    )
                    yield self._row(
                        "Aliases",
                        self._format_sequence(self.distribution.aliases),
                        "distribution-detail-aliases",
                    )
                    yield self._row(
                        "Origins",
                        self._format_sequence(self.distribution.origins),
                        "distribution-detail-origins",
                    )
                    yield self._row(
                        "Last modified",
                        self._format_datetime(self.distribution.last_modified_time),
                        "distribution-detail-last-modified",
                    )

                    yield Static("Logging", classes="distribution-detail-section-title")
                    yield self._row(
                        "Enabled",
                        self._format_log_summary(self.standard_log_deliveries),
                        "distribution-detail-logging-enabled",
                    )
                    yield self._row(
                        "Delivery IDs",
                        self._format_delivery_values(self.standard_log_deliveries, "delivery_id"),
                        "distribution-detail-logging-delivery-ids",
                    )
                    yield self._row(
                        "Destination type",
                        self._format_delivery_values(
                            self.standard_log_deliveries,
                            "delivery_destination_type",
                        ),
                        "distribution-detail-logging-destination-types",
                    )
                    yield self._row(
                        "Destination ARN",
                        self._format_delivery_values(
                            self.standard_log_deliveries,
                            "delivery_destination_arn",
                        ),
                        "distribution-detail-logging-destination-arns",
                    )
                    yield self._row(
                        "Source name",
                        self._format_delivery_values(
                            self.standard_log_deliveries,
                            "delivery_source_name",
                        ),
                        "distribution-detail-logging-source-names",
                    )

                    yield Static("Usage", classes="distribution-detail-section-title")
                    yield self._row(
                        "Download",
                        self._format_bytes(self.usage.download),
                        "distribution-detail-download",
                    )
                    yield self._row(
                        "Upload",
                        self._format_bytes(self.usage.upload),
                        "distribution-detail-upload",
                    )
                    yield self._row(
                        "Requests",
                        self._format_count(self.usage.requests),
                        "distribution-detail-requests",
                    )
                    yield self._row(
                        "Month",
                        self.usage.month_key or "-",
                        "distribution-detail-month",
                    )
                    yield self._row(
                        "Usage updated",
                        self._format_datetime(self.usage.last_updated),
                        "distribution-detail-usage-updated",
                    )

                yield Static("Actions", classes="distribution-detail-section-title")
                with Grid(id="distribution-detail-actions"):
                    yield Button(
                        "Cancel",
                        id="distribution-detail-cancel",
                        compact="compact",
                    )
                    yield Button(
                        "Save",
                        id="distribution-detail-save",
                        variant="primary",
                        compact="compact",
                    )

    def on_mount(self) -> None:
        self.query_one("#distribution-detail-type", Select).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "distribution-detail-save":
            selected = self.query_one("#distribution-detail-type", Select).value
            self.dismiss(normalize_distribution_type(selected))
        elif event.button.id == "distribution-detail-cancel":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    @staticmethod
    def _row(label: str, value: str | None, value_id: str) -> Horizontal:
        return Horizontal(
            Label(label, classes="distribution-detail-key"),
            Static(value or "-", id=value_id, classes="distribution-detail-value"),
            classes="distribution-detail-row",
        )

    def _row_type(self, label: str, value: str, value_id: str) -> Horizontal:
        return Horizontal(
            Label(label, classes="distribution-detail-key"),
            Select(
                [("PAYG", "PAYG"), ("Free", "Free")],
                value=value,
                id=value_id,
            ),
            classes="distribution-detail-row",
        )

    @staticmethod
    def _format_sequence(values: tuple[str, ...]) -> str:
        return ", ".join(values) if values else "-"

    @staticmethod
    def _format_delivery_values(
        deliveries: tuple[StandardLogDeliveryRecord, ...],
        field_name: str,
    ) -> str:
        values = [
            str(getattr(delivery, field_name, "")).strip()
            for delivery in deliveries
            if str(getattr(delivery, field_name, "")).strip()
        ]
        return "\n".join(values) if values else "-"

    @staticmethod
    def _format_log_summary(deliveries: tuple[StandardLogDeliveryRecord, ...]) -> str:
        if not deliveries:
            return "-"
        destination_types = {
            str(delivery.delivery_destination_type or "").strip().upper()
            for delivery in deliveries
            if str(delivery.delivery_destination_type or "").strip()
        }
        destination_types.discard("")
        if not destination_types:
            return "-"
        if len(destination_types) == 1:
            return next(iter(destination_types))
        return "MIX"

    @staticmethod
    def _format_datetime(value: datetime | None) -> str:
        if value is None:
            return "-"
        return f"{value:%Y-%m-%d %H:%M:%S}"

    @staticmethod
    def _format_bytes(value: int | None) -> str:
        if value is None:
            return "-"
        value_gb = Decimal(value) / BYTES_PER_GB
        return f"{value_gb.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} GB"

    @staticmethod
    def _format_count(value: int | None) -> str:
        if value is None:
            return "-"
        return f"{value:,}"
