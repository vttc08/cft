from __future__ import annotations

import pytest
from botocore.exceptions import (
    ClientError,
    ConfigParseError,
    NoCredentialsError,
    PartialCredentialsError,
    ProfileNotFound,
)

from cft.application.errors import dashboard_error_presentation


@pytest.mark.parametrize(
    ("error", "expected_title", "expected_text"),
    (
        (
            NoCredentialsError(),
            "AWS credentials not found",
            "~/.aws/credentials",
        ),
        (
            PartialCredentialsError(provider="env", cred_var="AWS_SECRET_ACCESS_KEY"),
            "AWS credentials are incomplete",
            "Provide both the access key ID and secret access key",
        ),
        (
            ConfigParseError(path="/tmp/credentials"),
            "AWS credential configuration is invalid",
            "Check the INI syntax",
        ),
        (
            ProfileNotFound(profile="missing"),
            "AWS profile not found",
            "--profile <name>",
        ),
        (
            ClientError(
                {
                    "Error": {
                        "Code": "InvalidClientTokenId",
                        "Message": "The security token included in the request is invalid.",
                    }
                },
                "GetCallerIdentity",
            ),
            "AWS credentials were rejected",
            "InvalidClientTokenId",
        ),
        (
            ClientError(
                {
                    "Error": {
                        "Code": "ExpiredToken",
                        "Message": "The security token included in the request is expired.",
                    }
                },
                "GetCallerIdentity",
            ),
            "AWS credentials have expired",
            "Refresh the session token",
        ),
    ),
)
def test_dashboard_error_presentation_explains_credential_failures(
    error: Exception,
    expected_title: str,
    expected_text: str,
) -> None:
    presentation = dashboard_error_presentation(
        error,
        profile_name="dev",
        stage="inventory",
    )

    assert presentation.title == expected_title
    assert expected_text in presentation.message
    assert "dev" in presentation.message


def test_dashboard_error_presentation_distinguishes_valid_but_unauthorized_credentials() -> None:
    error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Denied"}},
        "ListDistributions",
    )

    presentation = dashboard_error_presentation(
        error,
        profile_name="readonly",
        stage="inventory",
    )

    assert presentation.title == "AWS permission denied"
    assert "STS GetCallerIdentity" in presentation.message
    assert "CloudFront inventory" in presentation.message


@pytest.mark.parametrize(
    "error",
    (
        ImportError("No module named expat; use SimpleXMLTreeBuilder instead"),
        ModuleNotFoundError("No module named 'pyexpat'", name="pyexpat"),
        ImportError(
            'dlopen failed: library "libexpat.so.1" not found: needed by pyexpat'
        ),
    ),
)
def test_dashboard_error_presentation_explains_missing_python_xml_support(
    error: Exception,
) -> None:
    presentation = dashboard_error_presentation(
        error,
        profile_name="default",
        stage="inventory",
    )

    assert presentation.title == "Python XML support unavailable"
    assert "pyexpat" in presentation.message
    assert "not an AWS credentials problem" in presentation.message
    assert "pkg reinstall python libexpat" in presentation.message
    assert 'uv sync --python "$PREFIX/bin/python"' in presentation.message
    assert "Repair Python XML support" in presentation.help_text


def test_dashboard_error_presentation_does_not_misclassify_other_import_errors() -> None:
    presentation = dashboard_error_presentation(
        ModuleNotFoundError("No module named 'example'", name="example"),
        profile_name="default",
        stage="inventory",
    )

    assert presentation.title == "AWS inventory unavailable"
    assert "No module named 'example'" in presentation.message
