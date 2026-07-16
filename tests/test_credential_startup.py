from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from botocore.exceptions import (
    ConfigParseError,
    NoCredentialsError,
    PartialCredentialsError,
    ProfileNotFound,
)

from cft.application import DashboardLoadError
from cft.application.service import CftApplicationService
from cft.config.paths import AppPaths


def isolate_aws_credentials(monkeypatch, tmp_path) -> tuple[Path, Path]:
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_ROLE_ARN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        monkeypatch.delenv(key, raising=False)

    credentials_file = tmp_path / "credentials"
    config_file = tmp_path / "config"
    credentials_file.write_text("", encoding="utf-8")
    config_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials_file))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    return credentials_file, config_file


@pytest.mark.parametrize(
    ("profile_name", "configure", "expected_error"),
    (
        (None, lambda credentials, config: None, NoCredentialsError),
        (
            None,
            lambda credentials, config: credentials.write_text(
                "aws_access_key_id = only-one-value\n",
                encoding="utf-8",
            ),
            ConfigParseError,
        ),
        ("missing", lambda credentials, config: None, ProfileNotFound),
    ),
)
def test_application_wraps_shared_credential_startup_failures(
    monkeypatch,
    tmp_path,
    profile_name: str | None,
    configure: Callable[[Path, Path], object],
    expected_error: type[Exception],
) -> None:
    credentials_file, config_file = isolate_aws_credentials(monkeypatch, tmp_path)
    configure(credentials_file, config_file)
    application = CftApplicationService(
        profile_name=profile_name,
        paths=AppPaths.from_base(tmp_path / "cft"),
    )

    with pytest.raises(DashboardLoadError) as raised:
        application.load_dashboard(refresh=True)

    assert raised.value.stage == "inventory"
    assert isinstance(raised.value.error, expected_error)


def test_application_wraps_partial_environment_credentials(monkeypatch, tmp_path) -> None:
    isolate_aws_credentials(monkeypatch, tmp_path)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "only-one-value")
    application = CftApplicationService(paths=AppPaths.from_base(tmp_path / "cft"))

    with pytest.raises(DashboardLoadError) as raised:
        application.load_dashboard(refresh=True)

    assert isinstance(raised.value.error, PartialCredentialsError)
