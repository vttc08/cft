from __future__ import annotations

from dataclasses import dataclass

from botocore.exceptions import (
    ClientError,
    ConfigNotFound,
    ConfigParseError,
    CredentialRetrievalError,
    InvalidConfigError,
    NoCredentialsError,
    PartialCredentialsError,
    ProfileNotFound,
    RefreshWithMFAUnsupportedError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)


@dataclass(frozen=True)
class DashboardErrorPresentation:
    title: str
    message: str


class DashboardLoadError(RuntimeError):
    def __init__(self, stage: str, error: Exception) -> None:
        super().__init__(str(error))
        self.stage = stage
        self.error = error

    def presentation(self, *, profile_name: str) -> DashboardErrorPresentation:
        return dashboard_error_presentation(
            self.error,
            profile_name=profile_name,
            stage=self.stage,
        )


_REJECTED_CREDENTIAL_CODES = {
    "AuthFailure",
    "InvalidAccessKeyId",
    "InvalidClientTokenId",
    "InvalidSignatureException",
    "RequestExpired",
    "SignatureDoesNotMatch",
    "TokenRefreshRequired",
    "UnrecognizedClientException",
}
_EXPIRED_CREDENTIAL_CODES = {"ExpiredToken", "ExpiredTokenException"}
_ACCESS_DENIED_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}


def dashboard_error_presentation(
    error: Exception,
    *,
    profile_name: str,
    stage: str,
) -> DashboardErrorPresentation:
    """Translate SDK failures into concise, actionable frontend-safe text."""
    profile = profile_name or "default"

    if isinstance(error, NoCredentialsError):
        return DashboardErrorPresentation(
            title="AWS credentials not found",
            message=(
                f"No credentials were found for AWS profile '{profile}'. Add a complete "
                "profile to the shared AWS credentials file (normally ~/.aws/credentials), "
                "set both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or configure and sign "
                "in to AWS SSO."
            ),
        )

    if isinstance(error, PartialCredentialsError):
        return DashboardErrorPresentation(
            title="AWS credentials are incomplete",
            message=(
                f"Credentials for AWS profile '{profile}' are missing a required value "
                f"({error}). Provide both the access key ID and secret access key, and a "
                "session token when temporary credentials require one."
            ),
        )

    if isinstance(error, ProfileNotFound):
        return DashboardErrorPresentation(
            title="AWS profile not found",
            message=(
                f"AWS profile '{profile}' does not exist in the shared AWS configuration. "
                "Create that profile or restart cft with --profile <name> for an existing one."
            ),
        )

    if isinstance(error, (ConfigParseError, ConfigNotFound, InvalidConfigError)):
        return DashboardErrorPresentation(
            title="AWS credential configuration is invalid",
            message=(
                f"AWS could not read the configuration for profile '{profile}' ({error}). "
                "Check the INI syntax, file paths, and profile names in the shared AWS files "
                "(normally ~/.aws/config and ~/.aws/credentials)."
            ),
        )

    if isinstance(
        error,
        (
            CredentialRetrievalError,
            RefreshWithMFAUnsupportedError,
            SSOTokenLoadError,
            TokenRetrievalError,
            UnauthorizedSSOTokenError,
        ),
    ):
        return DashboardErrorPresentation(
            title="AWS credentials could not be refreshed",
            message=(
                f"AWS could not load renewable credentials for profile '{profile}' ({error}). "
                "Sign in again or repair the profile's SSO, role, credential_process, or token "
                "configuration."
            ),
        )

    if isinstance(error, ClientError):
        code = str(error.response.get("Error", {}).get("Code", "Unknown"))
        if code in _EXPIRED_CREDENTIAL_CODES:
            return DashboardErrorPresentation(
                title="AWS credentials have expired",
                message=(
                    f"AWS rejected the expired credentials for profile '{profile}' ({code}). "
                    "Refresh the session token, assumed role, or SSO login."
                ),
            )
        if code in _REJECTED_CREDENTIAL_CODES:
            return DashboardErrorPresentation(
                title="AWS credentials were rejected",
                message=(
                    f"AWS rejected the credentials for profile '{profile}' ({code}). Replace "
                    "or refresh the access key and session token; if the profile uses SSO or an "
                    "assumed role, sign in again or refresh its source credentials."
                ),
            )
        if code in _ACCESS_DENIED_CODES:
            return DashboardErrorPresentation(
                title="AWS permission denied",
                message=(
                    f"AWS accepted profile '{profile}' but denied this request ({code}). Ensure "
                    "the identity can call STS GetCallerIdentity and read CloudFront inventory."
                ),
            )

    if stage == "inventory":
        return DashboardErrorPresentation(
            title="AWS inventory unavailable",
            message=f"Could not load CloudFront inventory for profile '{profile}': {error}",
        )
    return DashboardErrorPresentation(
        title="CloudWatch usage unavailable",
        message=f"Could not load CloudWatch usage for profile '{profile}': {error}",
    )
