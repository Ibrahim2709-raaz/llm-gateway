import json
from collections import Counter

from app.cli.evaluate_classifier import run_evaluation
from app.routing.classifier_evaluation import evaluate_classifier, load_classifier_cases
from app.routing.taxonomy import TaskCategory, TaskDifficulty


def test_classifier_dataset_is_balanced_versioned_and_split() -> None:
    cases = load_classifier_cases()

    assert len(cases) == 30
    assert len({case.id for case in cases}) == len(cases)
    assert {case.split for case in cases} == {"tune", "heldout"}
    for split in ("tune", "heldout"):
        selected = [case for case in cases if case.split == split]
        assert Counter(case.category for case in selected) == {
            category: 3 for category in TaskCategory
        }
        assert Counter(case.difficulty for case in selected) == {
            difficulty: 5 for difficulty in TaskDifficulty
        }


def test_heldout_classifier_gate_passes() -> None:
    report = evaluate_classifier(load_classifier_cases(), split="heldout")

    assert report.passed is True
    assert report.macro_f1 >= 0.85
    assert report.minimum_category_recall >= 0.70
    assert report.case_count == 15
    assert report.latency_p95_ms >= 0


def test_classifier_evaluation_cli_returns_machine_readable_report() -> None:
    report = json.loads(run_evaluation("heldout"))

    assert report["dataset_version"] == "task-taxonomy-v1"
    assert report["classifier_version"] == "task-rules-v1"
    assert report["passed"] is True
