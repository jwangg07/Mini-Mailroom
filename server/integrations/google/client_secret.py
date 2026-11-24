"""Load the Google OAuth client secret from Secrets Manager."""

from integrations.aws_secret import JsonSecret, SecretUnavailableError

__all__ = ["GoogleClientSecret", "SecretUnavailableError"]


class GoogleClientSecret(JsonSecret):
    def __init__(self, secret_arn: str, cache_seconds: int = 300):
        super().__init__(
            secret_arn,
            "GOOGLE_CLIENT_SECRET",
            error_message="Google credentials unavailable",
            cache_seconds=cache_seconds,
        )
