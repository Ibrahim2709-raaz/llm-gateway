import json

from app.cli.classify_request import build_parser, classify_prompt


def test_classifier_cli_parser_and_json_output() -> None:
    arguments = build_parser().parse_args(
        ["Extract the customer fields as JSON", "--max-tokens", "256"]
    )

    output = classify_prompt(arguments.prompt, max_tokens=arguments.max_tokens)
    payload = json.loads(output)

    assert payload["category"] == "extraction"
    assert payload["difficulty"] == "simple"
    assert payload["confidence"] >= 0.65
    assert payload["classifier_version"] == "task-rules-v1"
    assert "extraction_direct" in payload["matched_signals"]
