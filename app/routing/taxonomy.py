from enum import StrEnum


class TaskCategory(StrEnum):
    CODING = "coding"
    REASONING = "reasoning"
    SUMMARIZATION = "summarization"
    EXTRACTION = "extraction"
    GENERAL_CHAT = "general_chat"


class TaskDifficulty(StrEnum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class ModelCapability(StrEnum):
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_USE = "tool_use"
    RETRIEVAL = "retrieval"


class VersionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class EvaluationRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationCaseOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REFUSED = "refused"


class ColdStartMode(StrEnum):
    STATIC_PROFILE = "static_profile"
    EXCLUDE = "exclude"
    EXPLICIT_ONLY = "explicit_only"


class PolicyActivationAction(StrEnum):
    ACTIVATE = "activate"
    ROLLBACK = "rollback"


TASK_CATEGORIES = tuple(category.value for category in TaskCategory)
TASK_DIFFICULTIES = tuple(difficulty.value for difficulty in TaskDifficulty)
MODEL_CAPABILITIES = tuple(capability.value for capability in ModelCapability)
VERSION_STATUSES = tuple(status.value for status in VersionStatus)
EVALUATION_RUN_STATUSES = tuple(status.value for status in EvaluationRunStatus)
EVALUATION_CASE_OUTCOMES = tuple(outcome.value for outcome in EvaluationCaseOutcome)
COLD_START_MODES = tuple(mode.value for mode in ColdStartMode)
POLICY_ACTIVATION_ACTIONS = tuple(action.value for action in PolicyActivationAction)
