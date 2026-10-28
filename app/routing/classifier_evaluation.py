import json
import math
from dataclasses import dataclass
from importlib import resources
from time import perf_counter_ns
from typing import Literal

from app.routing.classifier import CLASSIFIER_VERSION, classify_request
from app.routing.taxonomy import TaskCategory, TaskDifficulty
from app.schemas.chat import ChatCompletionRequest, ChatMessage

DATASET_VERSION = "task-taxonomy-v1"
MINIMUM_MACRO_F1 = 0.85
MINIMUM_CATEGORY_RECALL = 0.70
DatasetSplit = Literal["tune", "heldout"]


@dataclass(frozen=True, slots=True)
class ClassifierCase:
    id: str
    split: DatasetSplit
    prompt: str
    category: TaskCategory
    difficulty: TaskDifficulty
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CategoryMetrics:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True, slots=True)
class ClassifierEvaluation:
    dataset_version: str
    classifier_version: str
    split: DatasetSplit
    case_count: int
    category_metrics: dict[str, CategoryMetrics]
    macro_f1: float
    minimum_category_recall: float
    difficulty_accuracy: float
    latency_p95_ms: float
    passed: bool
    failures: tuple[str, ...]


def load_classifier_cases() -> tuple[ClassifierCase, ...]:
    dataset = resources.files("app.routing.datasets").joinpath("task_taxonomy_v1.jsonl")
    cases: list[ClassifierCase] = []
    seen_ids: set[str] = set()
    with dataset.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            case_id = str(payload["id"])
            if case_id in seen_ids:
                raise ValueError(f"Duplicate classifier case id '{case_id}'")
            seen_ids.add(case_id)
            try:
                split: DatasetSplit = payload["split"]
                case = ClassifierCase(
                    id=case_id,
                    split=split,
                    prompt=str(payload["prompt"]),
                    category=TaskCategory(payload["category"]),
                    difficulty=TaskDifficulty(payload["difficulty"]),
                    tags=tuple(str(tag) for tag in payload.get("tags", [])),
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"Invalid classifier case on line {line_number}") from error
            cases.append(case)
    return tuple(cases)


def evaluate_classifier(
    cases: tuple[ClassifierCase, ...],
    *,
    split: DatasetSplit = "heldout",
) -> ClassifierEvaluation:
    selected = tuple(case for case in cases if case.split == split)
    if not selected:
        raise ValueError(f"No classifier cases exist for split '{split}'")

    expected: list[TaskCategory] = []
    predicted: list[TaskCategory] = []
    difficulty_correct = 0
    latencies_ns: list[int] = []
    for case in selected:
        request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content=case.prompt)]
        )
        started = perf_counter_ns()
        result = classify_request(request)
        latencies_ns.append(perf_counter_ns() - started)
        expected.append(case.category)
        predicted.append(result.category)
        difficulty_correct += int(result.difficulty is case.difficulty)

    metrics = {
        category.value: _category_metrics(category, expected, predicted)
        for category in TaskCategory
    }
    macro_f1 = sum(metric.f1 for metric in metrics.values()) / len(metrics)
    minimum_recall = min(metric.recall for metric in metrics.values())
    failures: list[str] = []
    if macro_f1 < MINIMUM_MACRO_F1:
        failures.append("macro_f1_below_threshold")
    if minimum_recall < MINIMUM_CATEGORY_RECALL:
        failures.append("category_recall_below_threshold")
    return ClassifierEvaluation(
        dataset_version=DATASET_VERSION,
        classifier_version=CLASSIFIER_VERSION,
        split=split,
        case_count=len(selected),
        category_metrics=metrics,
        macro_f1=round(macro_f1, 4),
        minimum_category_recall=round(minimum_recall, 4),
        difficulty_accuracy=round(difficulty_correct / len(selected), 4),
        latency_p95_ms=round(_percentile_95(latencies_ns) / 1_000_000, 4),
        passed=not failures,
        failures=tuple(failures),
    )


def _category_metrics(
    category: TaskCategory,
    expected: list[TaskCategory],
    predicted: list[TaskCategory],
) -> CategoryMetrics:
    true_positive = sum(
        actual is category and guess is category
        for actual, guess in zip(expected, predicted, strict=True)
    )
    false_positive = sum(
        actual is not category and guess is category
        for actual, guess in zip(expected, predicted, strict=True)
    )
    false_negative = sum(
        actual is category and guess is not category
        for actual, guess in zip(expected, predicted, strict=True)
    )
    support = sum(actual is category for actual in expected)
    precision = true_positive / (true_positive + false_positive) if true_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    return CategoryMetrics(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        support=support,
    )


def _percentile_95(values: list[int]) -> int:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]
