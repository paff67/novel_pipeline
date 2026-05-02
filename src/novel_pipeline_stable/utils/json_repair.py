from __future__ import annotations

import json
import re
from typing import Any


def loads_json_fragment(raw_text: str) -> Any:
    candidate = raw_text.strip()
    if not candidate:
        raise ValueError("Model returned empty content instead of JSON.")
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    object_match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if object_match:
        return json.loads(object_match.group(0))
    array_match = re.search(r"\[.*\]", candidate, re.DOTALL)
    if array_match:
        return json.loads(array_match.group(0))
    raise ValueError(f"Model did not return valid JSON. Raw content starts with: {candidate[:200]!r}")


def repair_instruction_rules() -> list[str]:
    return [
        "Return exactly one valid JSON object.",
        "Preserve the semantic content of the broken JSON when possible.",
        "Do not add explanatory prose.",
        "Do not add extra keys outside the template.",
        "If the broken JSON already expresses a refusal or fallback shape, preserve that shape.",
        "If a list item cannot be recovered confidently, drop that item instead of returning a blank placeholder object.",
        "If no valid items remain for a list field, return an empty array for that field.",
        "Otherwise, if a field cannot be recovered confidently, use an empty string or empty array instead of inventing content.",
    ]


__all__ = [
    "loads_json_fragment",
    "repair_instruction_rules",
]
