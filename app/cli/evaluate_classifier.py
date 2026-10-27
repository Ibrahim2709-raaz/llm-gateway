import argparse
import json
from dataclasses import asdict

from app.routing.classifier_evaluation import evaluate_classifier, load_classifier_cases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the deterministic task classifier.")
    parser.add_argument("--split", choices=("tune", "heldout"), default="heldout")
    return parser


def run_evaluation(split: str) -> str:
    if split not in {"tune", "heldout"}:
        raise ValueError(f"Unknown classifier split '{split}'")
    report = evaluate_classifier(load_classifier_cases(), split=split)  # type: ignore[arg-type]
    return json.dumps(asdict(report), indent=2)


def main() -> None:
    arguments = build_parser().parse_args()
    report = run_evaluation(arguments.split)
    print(report)
    if not json.loads(report)["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
