from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class FirstRunOnboardingScreen(ModalScreen[bool]):
    BINDINGS = [
        ("escape", "continue_", "Continue"),
        ("q", "continue_", "Continue"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="onboarding-modal"):
            with Vertical(id="onboarding-dialog", classes="panel"):
                yield Static("Welcome to cft", id="onboarding-title")
                with VerticalScroll(id="onboarding-content"):
                    yield Static(
                        "This screen appears only once and can be updated later.",
                        id="onboarding-subtitle",
                    )
                    yield Static(
                        (
                            "Why the data may be limited:\n"
                            "- CloudFront inventory comes from AWS access.\n"
                            "- Current-month usage depends on CloudWatch metrics.\n"
                            "- Billing totals depend on a linked AWS Data Export / CUR 2.0 delivery.\n"
                            "- Upload visibility improves when CloudFront standard logs are linked.\n"
                        ),
                        id="onboarding-warning",
                    )
                    yield Static(
                        (
                            "Helpful shortcuts:\n"
                            "- r refreshes data\n"
                            "- Enter opens a distribution\n"
                            "- Ctrl+P opens configuration\n"
                            "- b opens configuration\n"
                            "- q closes screens or quits\n"
                        ),
                        id="onboarding-shortcuts",
                    )
                    yield Static(
                        (
                            "Setup hints:\n"
                            "- Link an AWS Data Export / CUR 2.0 bucket, prefix, and export name.\n"
                            "- Configure distribution-specific logging if you want upload visibility.\n"
                            "- Save profile-scoped overrides under ~/.cft/config/.\n"
                        ),
                        id="onboarding-setup",
                    )
                yield Button(
                    "Continue",
                    id="onboarding-continue",
                    variant="primary",
                    compact="compact",
                )

    def on_mount(self) -> None:
        self.query_one("#onboarding-continue", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "onboarding-continue":
            self.dismiss(True)

    def action_continue_(self) -> None:
        self.dismiss(True)
