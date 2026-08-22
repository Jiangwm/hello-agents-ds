from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import Scenario


SCENARIO_FILES = {
    "upstream_slowdown": "upstream_slowdown.json",
    "quality_isolation": "quality_isolation.json",
    "peak_tariff_urgent_order": "peak_tariff_urgent_order.json",
}


def scenario_root() -> Path:
    return Path(__file__).resolve().parents[2] / "scenarios"


def load_scenario(scenario_id: str) -> tuple[Scenario, str, str]:
    if scenario_id not in SCENARIO_FILES:
        raise ValueError(f"scenario is not whitelisted: {scenario_id}")
    path = (scenario_root() / SCENARIO_FILES[scenario_id]).resolve()
    if path.parent != scenario_root().resolve() or not path.is_file():
        raise ValueError("whitelisted scenario file is unavailable")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    scenario = Scenario.model_validate(json.loads(raw.decode("utf-8")))
    if scenario.scenario_id != scenario_id:
        raise ValueError("scenario id does not match whitelist entry")
    return scenario, digest, f"scenario-load-{digest[:16]}"


def list_scenarios() -> list[dict[str, str | bool]]:
    result = []
    for scenario_id in SCENARIO_FILES:
        scenario, digest, tool_call_id = load_scenario(scenario_id)
        result.append(
            {
                "scenario_id": scenario.scenario_id,
                "name": scenario.name,
                "description": scenario.description,
                "data_version": scenario.data_version,
                "sha256": digest,
                "tool_call_id": tool_call_id,
                "deidentified": True,
                "read_only": True,
            }
        )
    return result
