from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from cft.aws.cloudwatch_logs import CloudWatchLogGroupSummary
from cft.config.settings import display_cwl_log_group


@dataclass(frozen=True)
class CwlLogGroupStatus:
    log_group: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.log_group and str(self.log_group).strip())


@dataclass(frozen=True)
class CwlLogGroupSetupResult:
    log_group: str


class CwlLogGroupSetupScreen(ModalScreen[CwlLogGroupSetupResult | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        profile_name: str,
        log_groups: tuple[CloudWatchLogGroupSummary, ...],
        initial_status: CwlLogGroupStatus,
        error_message: str | None = None,
    ) -> None:
        super().__init__()
        self.profile_name = profile_name
        self.log_groups = log_groups
        self.initial_status = initial_status
        self.error_message = error_message

    def compose(self) -> ComposeResult:
        initial_index = self._initial_index()
        with Container(id="cwl-log-group-modal"):
            with Vertical(id="cwl-log-group-dialog", classes="panel"):
                yield Static(
                    f"Link CloudWatch Logs override for profile {self.profile_name}",
                    id="cwl-log-group-title",
                )
                yield Static(self.error_message or "", id="cwl-log-group-error")
                yield Label("Discovered log groups", classes="cwl-log-group-label")
                if self.log_groups:
                    yield ListView(
                        *[
                            ListItem(
                                Label(self._log_group_list_label(log_group)),
                                id=f"log-group-{index}",
                            )
                            for index, log_group in enumerate(self.log_groups)
                        ],
                        initial_index=initial_index,
                        id="cwl-log-group-list",
                    )
                    yield Static("", id="cwl-log-group-selection")
                else:
                    yield Static(
                        "No CloudWatch log groups available for this profile.",
                        id="cwl-log-group-empty",
                    )
                yield Label("Override ARN or name", classes="cwl-log-group-label")
                yield Input(
                    value=self.initial_status.log_group or "",
                    placeholder="log-group-name or ARN",
                    id="cwl-log-group-input",
                )
                with Horizontal(id="cwl-log-group-actions"):
                    yield Button("Save", id="cwl-log-group-save", variant="primary", compact="compact")
                    yield Button("Cancel", id="cwl-log-group-cancel", compact="compact")

    def on_mount(self) -> None:
        self._refresh_state()
        self._refresh_selection_preview()
        if self.log_groups:
            self.query_one("#cwl-log-group-list", ListView).focus()
        else:
            self.query_one("#cwl-log-group-input", Input).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "cwl-log-group-list":
            selected_value = self._selected_log_group_value()
            if selected_value:
                self.query_one("#cwl-log-group-input", Input).value = selected_value
            self.query_one("#cwl-log-group-input", Input).focus()
            self._refresh_selection_preview()
            self._refresh_state()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "cwl-log-group-list":
            self._refresh_selection_preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "cwl-log-group-input":
            self._refresh_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cwl-log-group-cancel":
            self.dismiss(None)
            return
        if event.button.id == "cwl-log-group-save":
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        log_group = self.query_one("#cwl-log-group-input", Input).value.strip()
        if not log_group:
            self._set_error("Log group ARN or name is required.")
            return

        self.dismiss(CwlLogGroupSetupResult(log_group=log_group))

    def _refresh_state(self) -> None:
        self.query_one("#cwl-log-group-save", Button).disabled = not bool(
            self.query_one("#cwl-log-group-input", Input).value.strip()
        )

    def _refresh_selection_preview(self) -> None:
        if not self.log_groups:
            return
        log_group = self._selected_log_group()
        preview = self.query_one("#cwl-log-group-selection", Static)
        if log_group is None:
            preview.update("Selected: -")
            return
        preview.update(
            "Selected: "
            f"{log_group.log_group_name} · {log_group.log_group_class or '-'} · "
            f"{display_cwl_log_group(log_group.log_group_arn)}"
        )

    def _selected_log_group(self) -> CloudWatchLogGroupSummary | None:
        if not self.log_groups:
            return None
        list_view = self.query_one("#cwl-log-group-list", ListView)
        if list_view.index is None:
            return None
        if list_view.index < 0 or list_view.index >= len(self.log_groups):
            return None
        return self.log_groups[list_view.index]

    def _selected_log_group_value(self) -> str | None:
        log_group = self._selected_log_group()
        if log_group is None:
            return None
        return log_group.log_group_arn or log_group.log_group_name

    def _initial_index(self) -> int:
        if not self.initial_status.log_group:
            return 0
        target = self._normalize(self.initial_status.log_group)
        for index, log_group in enumerate(self.log_groups):
            if target in {
                self._normalize(getattr(log_group, "log_group_arn", None)),
                self._normalize(getattr(log_group, "log_group_name", None)),
            }:
                return index
        return 0

    @staticmethod
    def _normalize(value: object | None) -> str:
        text = str(value or "").strip()
        if text.endswith(":*"):
            return text[:-2]
        if text.endswith("*"):
            return text[:-1]
        return text

    @staticmethod
    def _log_group_list_label(log_group: CloudWatchLogGroupSummary) -> str:
        name = str(log_group.log_group_name).strip() or "-"
        group_class = str(log_group.log_group_class or "").strip() or "-"
        return f"{name} · {group_class}"

    def _set_error(self, message: str) -> None:
        self.query_one("#cwl-log-group-error", Static).update(message)
