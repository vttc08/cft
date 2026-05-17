from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from cft.config.settings import display_cwl_log_group, display_data_export_prefix
from cft.tui.screens.cwl_logs_setup import CwlLogGroupStatus
from cft.tui.screens.cur_export_setup import CurExportStatus


class ConfigurationMenuScreen(ModalScreen[str | None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
    ]

    def __init__(
        self,
        *,
        profile_name: str,
        cur_export_status: CurExportStatus,
        cwl_log_group_status: CwlLogGroupStatus,
    ) -> None:
        super().__init__()
        self.profile_name = profile_name
        self.cur_export_status = cur_export_status
        self.cwl_log_group_status = cwl_log_group_status

    def compose(self) -> ComposeResult:
        with Container(id="configuration-menu-modal"):
            with Vertical(id="configuration-menu-dialog", classes="panel"):
                yield Static(
                    f"Edit Configuration for profile {self.profile_name}",
                    id="configuration-menu-title",
                )
                yield Static(
                    "Manage CUR Data Export and CloudWatch Logs overrides.",
                    id="configuration-menu-subtitle",
                )

                with Vertical(classes="configuration-menu-card"):
                    yield Static("CUR Export", classes="configuration-menu-card-title")
                    yield Static(self._cur_export_detail(), classes="configuration-menu-card-detail")
                    with Horizontal(classes="configuration-menu-actions"):
                        yield Button(
                            "Edit Data Export",
                            id="configuration-menu-edit-cur-export",
                            variant="primary",
                            compact="compact",
                        )

                with Vertical(classes="configuration-menu-card"):
                    yield Static("CWL Logs", classes="configuration-menu-card-title")
                    yield Static(self._cwl_detail(), classes="configuration-menu-card-detail")
                    with Horizontal(classes="configuration-menu-actions"):
                        yield Button(
                            "Edit CWL Logs",
                            id="configuration-menu-edit-cwl",
                            variant="primary",
                            compact="compact",
                        )

                with Horizontal(id="configuration-menu-footer"):
                    yield Button("Close", id="configuration-menu-close", compact="compact")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "configuration-menu-edit-cur-export":
            self.dismiss("cur_export")
        elif event.button.id == "configuration-menu-edit-cwl":
            self.dismiss("cwl_logs")
        elif event.button.id == "configuration-menu-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def _cur_export_detail(self) -> str:
        if not self.cur_export_status.is_configured:
            return "Not configured"
        return (
            f"Bucket: {self.cur_export_status.bucket or '-'}"
            f" · Path: {display_data_export_prefix(self.cur_export_status.prefix)}"
            f" · Export: {self.cur_export_status.export_name or '-'}"
        )

    def _cwl_detail(self) -> str:
        if not self.cwl_log_group_status.is_configured:
            return "Not configured"
        return f"Override: {display_cwl_log_group(self.cwl_log_group_status.log_group)}"
