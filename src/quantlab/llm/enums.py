"""Provider-neutral message and capability vocabulary."""
from enum import StrEnum


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ImageMediaType(StrEnum):
    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    REFUSAL = "refusal"
    CONTENT_FILTER = "content_filter"
    OTHER = "other"
