from decimal import Decimal

import pytest

from app.evaluations.scoring import score_output


def test_exact_match_is_deterministic_and_case_insensitive_by_default() -> None:
    result = score_output(
        "  PARIS ",
        expected_output={"text": "Paris"},
        configuration={"method": "exact_match"},
    )

    assert result.score == Decimal("1")
    assert result.metrics == {"method": "exact_match", "matched": True}


def test_contains_all_reports_partial_coverage() -> None:
    result = score_output(
        "Redis provides counters and caching.",
        expected_output={"required_phrases": ["Redis", "caching", "rate limiting"]},
        configuration={"method": "contains_all"},
    )

    assert result.score == Decimal("2") / Decimal("3")
    assert result.metrics["matched_count"] == 2


@pytest.mark.parametrize(
    ("output", "score", "valid_json"),
    [
        ('{"name":"Ada","year":1843}', Decimal("1"), True),
        ('{"name":"Ada"}', Decimal("0.5"), True),
        ("not json", Decimal("0"), False),
    ],
)
def test_json_field_scoring(output: str, score: Decimal, valid_json: bool) -> None:
    result = score_output(
        output,
        expected_output={"required_fields": ["name", "year"]},
        configuration={"method": "json_fields"},
    )

    assert result.score == score
    assert result.metrics["valid_json"] is valid_json


def test_unknown_scoring_method_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        score_output("answer", expected_output={}, configuration={"method": "judge"})
