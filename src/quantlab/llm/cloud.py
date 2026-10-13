"""Server-only free-only admission. Operator attestations are not pricing discovery."""
from datetime import datetime, timedelta, timezone
import os
import re
from threading import Lock
from typing import Literal

from pydantic import AwareDatetime, Field, SecretStr
from .errors import LLMError
from .models import _Contract


class CloudError(LLMError):
    def __init__(self, code: str):
        self.code = code
        super().__init__()


class FreeTierVerification(_Contract):
    provider_id: Literal["gemini"] = "gemini"
    model_id: str = Field(pattern=r"^gemini-[a-z0-9.-]{1,80}$")
    checked_at: AwareDatetime
    expires_at: AwareDatetime
    billing_disabled: Literal[True]
    free_inference_eligible: Literal[True]
    structured_text_supported: Literal[True]

    def current(self, now: datetime) -> bool:
        return (self.checked_at.utcoffset() == timedelta(0)
            and self.expires_at.utcoffset() == timedelta(0)
            and self.checked_at <= now < self.expires_at
            and timedelta(0) < self.expires_at - self.checked_at <= timedelta(days=7)
            and not self.model_id.endswith("latest"))


class CloudSettings(_Contract):
    enabled: bool = False
    provider_id: str = "gemini"
    model_id: str = ""
    provider_allowlist: tuple[str, ...] = ("gemini",)
    model_allowlist: tuple[str, ...] = ()
    free_only: Literal[True] = True
    verification: FreeTierVerification | None = None
    api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    max_input_tokens: int = Field(default=32768, ge=1024, le=32768)
    max_output_tokens: int = Field(default=4096, ge=128, le=4096)
    max_prompt_bytes: int = Field(default=49152, ge=1024, le=65536)
    max_response_bytes: int = Field(default=65536, ge=1024, le=65536)
    max_session_requests: int = Field(default=20, ge=1, le=100)
    timeout_ms: int = Field(default=20000, ge=1000, le=30000)

    def rejection(self, now: datetime | None = None) -> str | None:
        now = now or datetime.now(timezone.utc)
        if not self.enabled:
            return "cloud_disabled"
        if self.provider_id != "gemini" or self.provider_id not in self.provider_allowlist:
            return "unsupported_provider"
        if (not re.fullmatch(r"gemini-[a-z0-9.-]{1,80}", self.model_id)
                or self.model_id not in self.model_allowlist or self.verification is None
                or self.verification.model_id != self.model_id or not self.verification.current(now)):
            return "free_policy_rejected"
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            return "credentials_missing"
        return None

    @classmethod
    def from_env(cls):
        try:
            enabled = os.environ.get("QUANTLAB_CLOUD_ENABLED", "false")
            if enabled not in ("true", "false"):
                raise ValueError()
            evidence = os.environ.get("QUANTLAB_CLOUD_FREE_VERIFICATION")
            return cls(enabled=enabled == "true",
                provider_id=os.environ.get("QUANTLAB_CLOUD_PROVIDER", "gemini"),
                model_id=os.environ.get("QUANTLAB_CLOUD_MODEL", ""),
                model_allowlist=tuple(filter(None, os.environ.get("QUANTLAB_CLOUD_MODEL_ALLOWLIST", "").split(","))),
                verification=FreeTierVerification.model_validate_json(evidence, strict=True) if evidence else None,
                api_key=SecretStr(os.environ["GEMINI_API_KEY"]) if os.environ.get("GEMINI_API_KEY") else None,
                max_session_requests=int(os.environ.get("QUANTLAB_CLOUD_MAX_REQUESTS", "20")),
                timeout_ms=int(os.environ.get("QUANTLAB_CLOUD_TIMEOUT_MS", "20000")))
        except (ValueError, TypeError):
            raise RuntimeError("Invalid server cloud configuration.") from None


class CloudBudget:
    """Process-session ledger. Reserve count+generate before I/O; no refunds/retries."""
    def __init__(self, settings: CloudSettings):
        self.settings = CloudSettings.model_validate(settings)
        self._lock = Lock()
        self.used = 0
        self.active = False

    def acquire(self):
        with self._lock:
            code = self.settings.rejection()
            if code:
                raise CloudError(code)
            if self.active:
                raise CloudError("cloud_busy")
            if self.used + 2 > self.settings.max_session_requests:
                raise CloudError("budget_exhausted")
            self.used += 2
            self.active = True

    def release(self):
        with self._lock:
            self.active = False

    def snapshot(self):
        with self._lock:
            return self.used, self.active
