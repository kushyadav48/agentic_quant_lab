"""Fail-closed, server-controlled local configuration. No implicit .env loading."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class APISettings(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True,
        revalidate_instances="always", validate_default=True)

    operator_token: SecretStr = Field(repr=False)
    reader_token: SecretStr | None = Field(default=None, repr=False)
    journal_path: Path | None = Field(default=None, repr=False)
    cors_origins: tuple[str, ...] = ()
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=1_048_576)
    max_concurrent_requests: int = Field(default=16, ge=1, le=64)
    port: int = Field(default=8000, ge=1024, le=65535)

    @model_validator(mode="after")
    def safe_local_configuration(self):
        tokens = [t.get_secret_value() for t in (self.operator_token, self.reader_token) if t is not None]
        if any(not 32 <= len(t) <= 256 or not t.isascii() or any(c.isspace() for c in t) for t in tokens):
            raise ValueError("API credentials must be 32-256 non-whitespace ASCII characters")
        if len(tokens) != len(set(tokens)):
            raise ValueError("Reader and operator credentials must differ")
        for origin in self.cors_origins:
            url = urlsplit(origin)
            if (url.scheme not in ("http", "https") or url.hostname not in ("localhost", "127.0.0.1", "::1")
                    or url.username or url.password or url.path or url.query or url.fragment):
                raise ValueError("CORS requires exact local origins")
            _ = url.port  # Reject malformed ports.
        return self

    @classmethod
    def from_env(cls):
        try:
            return cls(
                operator_token=SecretStr(os.environ["QUANTLAB_API_OPERATOR_TOKEN"]),
                reader_token=SecretStr(os.environ["QUANTLAB_API_READER_TOKEN"])
                if os.environ.get("QUANTLAB_API_READER_TOKEN") else None,
                journal_path=Path(os.environ["QUANTLAB_API_JOURNAL_PATH"])
                if os.environ.get("QUANTLAB_API_JOURNAL_PATH") else None,
                cors_origins=tuple(filter(None, os.environ.get("QUANTLAB_API_CORS_ORIGINS", "").split(","))),
                port=int(os.environ.get("QUANTLAB_API_PORT", "8000")),
            )
        except (KeyError, ValueError):
            raise RuntimeError("Invalid API configuration; set a valid operator credential and local settings.") from None
