from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from cft.config.settings import display_data_export_prefix


@dataclass(frozen=True)
class CurExportStatus:
    bucket: str | None = None
    prefix: str | None = None
    export_name: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.bucket and self.export_name)


@dataclass(frozen=True)
class CurExportSetupResult:
    bucket: str
    prefix: str | None
    export_name: str


class CurExportSetupScreen(ModalScreen[CurExportSetupResult | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        profile_name: str,
        bucket_names: tuple[str, ...],
        initial_status: CurExportStatus,
        error_message: str | None = None,
    ) -> None:
        super().__init__()
        self.profile_name = profile_name
        self.bucket_names = bucket_names
        self.initial_status = initial_status
        self.error_message = error_message

    def compose(self) -> ComposeResult:
        initial_bucket_index = 0
        if self.initial_status.bucket and self.initial_status.bucket in self.bucket_names:
            initial_bucket_index = self.bucket_names.index(self.initial_status.bucket)

        with Container(id="cur-export-modal"):
            with Vertical(id="cur-export-dialog", classes="panel"):
                yield Static(
                    f"Link CUR export for profile {self.profile_name}",
                    id="cur-export-title",
                )
                yield Static(self.error_message or "", id="cur-export-error")
                yield Label("S3 bucket", classes="cur-export-label")
                if self.bucket_names:
                    yield ListView(
                        *[
                            ListItem(Label(bucket_name), id=f"bucket-{index}")
                            for index, bucket_name in enumerate(self.bucket_names)
                        ],
                        initial_index=initial_bucket_index,
                        id="cur-export-bucket-list",
                    )
                else:
                    yield Static(
                        "No S3 buckets available for this profile.",
                        id="cur-export-empty",
                    )
                yield Label("Bucket path", classes="cur-export-label")
                yield Input(
                    value=display_data_export_prefix(self.initial_status.prefix),
                    placeholder="/",
                    id="cur-export-prefix",
                )
                yield Label("Export name", classes="cur-export-label")
                yield Input(
                    value=self.initial_status.export_name or "",
                    placeholder="required",
                    id="cur-export-export-name",
                )
                with Horizontal(id="cur-export-actions"):
                    yield Button("Save", id="cur-export-save", variant="primary", compact="compact")
                    yield Button("Cancel", id="cur-export-cancel", compact="compact")

    def on_mount(self) -> None:
        self._refresh_state()
        if self.bucket_names:
            self.query_one("#cur-export-bucket-list", ListView).focus()
        else:
            self.query_one("#cur-export-prefix", Input).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "cur-export-bucket-list":
            self.query_one("#cur-export-export-name", Input).focus()
            self._refresh_state()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "cur-export-bucket-list":
            self._refresh_state()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {"cur-export-prefix", "cur-export-export-name"}:
            self._refresh_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cur-export-cancel":
            self.dismiss(None)
            return
        if event.button.id == "cur-export-save":
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        bucket = self._selected_bucket_name()
        export_name = self.query_one("#cur-export-export-name", Input).value.strip()
        if not bucket:
            self._set_error("Select an S3 bucket before saving.")
            return
        if not export_name:
            self._set_error("Export name is required.")
            return

        self.dismiss(
            CurExportSetupResult(
                bucket=bucket,
                prefix=self.query_one("#cur-export-prefix", Input).value,
                export_name=export_name,
            )
        )

    def _refresh_state(self) -> None:
        self.query_one("#cur-export-save", Button).disabled = not bool(
            self._selected_bucket_name()
            and self.query_one("#cur-export-export-name", Input).value.strip()
        )

    def _selected_bucket_name(self) -> str | None:
        if not self.bucket_names:
            return None
        bucket_list = self.query_one("#cur-export-bucket-list", ListView)
        if bucket_list.index is None:
            return None
        if bucket_list.index < 0 or bucket_list.index >= len(self.bucket_names):
            return None
        return self.bucket_names[bucket_list.index]

    def _set_error(self, message: str) -> None:
        self.query_one("#cur-export-error", Static).update(message)
