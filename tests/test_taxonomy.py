from app.routing.taxonomy import (
    EVALUATION_RUN_STATUSES,
    MODEL_CAPABILITIES,
    TASK_CATEGORIES,
    TASK_DIFFICULTIES,
    VERSION_STATUSES,
    EvaluationRunStatus,
    ModelCapability,
    TaskCategory,
    TaskDifficulty,
    VersionStatus,
)


def test_initial_task_taxonomy_is_stable() -> None:
    assert TASK_CATEGORIES == (
        "coding",
        "reasoning",
        "summarization",
        "extraction",
        "general_chat",
    )
    assert TASK_DIFFICULTIES == ("simple", "medium", "complex")


def test_taxonomy_values_serialize_as_strings() -> None:
    assert str(TaskCategory.CODING) == "coding"
    assert str(TaskDifficulty.COMPLEX) == "complex"
    assert str(ModelCapability.STRUCTURED_OUTPUT) == "structured_output"
    assert str(VersionStatus.ACTIVE) == "active"
    assert str(EvaluationRunStatus.COMPLETED) == "completed"


def test_supported_capabilities_and_lifecycle_values_are_explicit() -> None:
    assert MODEL_CAPABILITIES == (
        "text_generation",
        "structured_output",
        "tool_use",
        "retrieval",
    )
    assert VERSION_STATUSES == ("draft", "active", "retired")
    assert EVALUATION_RUN_STATUSES == ("pending", "running", "completed", "failed")
