from __future__ import annotations

import re


def redact_sensitive_text(text: str) -> str:
    redacted = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED_EMAIL]",
        text,
    )
    return re.sub(
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "[REDACTED_PHONE]",
        redacted,
    )
