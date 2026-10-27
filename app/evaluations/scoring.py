import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: Decimal | None
    metrics: dict[str, Any]


def score_output(
    actual_text: str,
    *,
    expected_output: dict[str, Any] | None,
    configuration: dict[str, Any],
) -> ScoreResult:
    method = configuration.get("method", "unscored")
    if method == "unscored":
        return ScoreResult(None, {"method": "unscored"})
    if method == "exact_match":
        expected = _required_string(expected_output, "text")
        case_sensitive = bool(configuration.get("case_sensitive", False))
        actual_value = actual_text.strip()
        expected_value = expected.strip()
        if not case_sensitive:
            actual_value = actual_value.casefold()
            expected_value = expected_value.casefold()
        matched = actual_value == expected_value
        return ScoreResult(
            Decimal("1") if matched else Decimal("0"),
            {"method": method, "matched": matched},
        )
    if method == "contains_all":
        required = _required_string_list(expected_output, "required_phrases")
        normalized = actual_text.casefold()
        matches = [phrase.casefold() in normalized for phrase in required]
        matched_count = sum(matches)
        score = Decimal(matched_count) / Decimal(len(required))
        return ScoreResult(
            score,
            {
                "method": method,
                "matched_count": matched_count,
                "required_count": len(required),
            },
        )
    if method == "json_fields":
        required_fields = _required_string_list(expected_output, "required_fields")
        try:
            payload = json.loads(actual_text)
        except json.JSONDecodeError:
            return ScoreResult(
                Decimal("0"),
                {"method": method, "valid_json": False, "matched_count": 0},
            )
        if not isinstance(payload, dict):
            return ScoreResult(
                Decimal("0"),
                {"method": method, "valid_json": True, "matched_count": 0},
            )
        matched_count = sum(field in payload for field in required_fields)
        score = Decimal(matched_count) / Decimal(len(required_fields))
        return ScoreResult(
            score,
            {
                "method": method,
                "valid_json": True,
                "matched_count": matched_count,
                "required_count": len(required_fields),
            },
        )
    raise ValueError(f"Unsupported deterministic scoring method '{method}'")


def _required_string(payload: dict[str, Any] | None, key: str) -> str:
    value = payload.get(key) if payload else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected output must contain non-empty string '{key}'")
    return value


def _required_string_list(payload: dict[str, Any] | None, key: str) -> list[str]:
    value = payload.get(key) if payload else None
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"Expected output must contain non-empty string list '{key}'")
    return value
