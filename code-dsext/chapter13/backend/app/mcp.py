from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from .models import MCPReadResult


class ReadOnlyEnergyMCPClient:
    allowed_resources = {
        "metadata",
        "tariff_profiles",
        "tasks",
        "constraints",
        "energy_baselines",
    }

    def __init__(self, sample_data_dir: Path | str):
        self.sample_data_dir = Path(sample_data_dir).resolve()

    def read(self, resource: str) -> MCPReadResult:
        if resource not in self.allowed_resources:
            raise ValueError(f"unsupported read-only resource: {resource}")
        path = (self.sample_data_dir / f"{resource}.json").resolve()
        if path.parent != self.sample_data_dir:
            raise ValueError("resource must remain inside sample_data")
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        metadata = self._metadata()
        content_hash = hashlib.sha256(raw).hexdigest()
        call_name = f"{resource}:{content_hash}"
        return MCPReadResult(
            payload=payload,
            tool_call_id=f"tool-{uuid.uuid5(uuid.NAMESPACE_URL, call_name)}",
            content_hash=content_hash,
            source_window=metadata["source_window"],
            data_version=metadata["data_version"],
        )

    def _metadata(self) -> dict:
        raw = (self.sample_data_dir / "metadata.json").read_text(encoding="utf-8")
        return json.loads(raw)
