import pytest

from app.routing.classifier import CLASSIFIER_VERSION, classify_request
from app.routing.taxonomy import TaskCategory, TaskDifficulty
from app.schemas.chat import ChatCompletionRequest, ChatMessage


def request(
    content: str,
    *,
    messages: list[ChatMessage] | None = None,
) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        messages=messages or [ChatMessage(role="user", content=content)]
    )


@pytest.mark.parametrize(
    ("prompt", "category", "signal"),
    [
        ("Debug this Python traceback", TaskCategory.CODING, "coding_debugging"),
        ("Prove this theorem step-by-step", TaskCategory.REASONING, "reasoning_formal"),
        ("Summarize this report", TaskCategory.SUMMARIZATION, "summarization_direct"),
        ("Extract the entities as JSON", TaskCategory.EXTRACTION, "extraction_direct"),
    ],
)
def test_classifier_selects_primary_category_with_safe_signal(
    prompt: str, category: TaskCategory, signal: str
) -> None:
    result = classify_request(request(prompt))

    assert result.category is category
    assert signal in result.matched_signals
    assert result.confidence >= 0.65
    assert result.reason_codes[0] == f"category_{category.value}_signal"
    assert result.classifier_version == CLASSIFIER_VERSION


def test_multi_signal_request_selects_stronger_primary_category() -> None:
    result = classify_request(request("Summarize this Python function and list the key points"))

    assert result.category is TaskCategory.SUMMARIZATION
    assert "summarization_direct" in result.matched_signals
    assert "coding_language" in result.matched_signals


def test_tied_category_signals_fall_back_to_general_chat() -> None:
    result = classify_request(request("Write a function and compare the trade-offs"))

    assert result.category is TaskCategory.GENERAL_CHAT
    assert result.confidence == 0.5
    assert "ambiguous_category_tie" in result.matched_signals
    assert "low_confidence_general_fallback" in result.reason_codes
    assert result.difficulty is TaskDifficulty.MEDIUM


def test_no_signal_uses_safe_general_chat_fallback() -> None:
    result = classify_request(request("What should I do today?"))

    assert result.category is TaskCategory.GENERAL_CHAT
    assert result.confidence == 0.55
    assert result.matched_signals == ()
    assert result.reason_codes == (
        "no_category_signal_general_fallback",
        "difficulty_medium_low_confidence",
    )


@pytest.mark.parametrize(
    ("prompt", "difficulty"),
    [
        ("Summarize this note", TaskDifficulty.SIMPLE),
        ("Write a Python function with tests and edge cases", TaskDifficulty.MEDIUM),
        ("Debug the root cause of this architecture", TaskDifficulty.COMPLEX),
        ("x" * 3200, TaskDifficulty.COMPLEX),
    ],
)
def test_difficulty_uses_task_and_structure_signals(
    prompt: str, difficulty: TaskDifficulty
) -> None:
    assert classify_request(request(prompt)).difficulty is difficulty


def test_tool_context_is_always_treated_as_complex() -> None:
    result = classify_request(
        request(
            "Use this result",
            messages=[
                ChatMessage(role="user", content="Check this"),
                ChatMessage(role="tool", content="tool output"),
            ],
        )
    )

    assert result.difficulty is TaskDifficulty.COMPLEX
    assert "difficulty_complex_tool_context" in result.reason_codes


def test_short_conversation_is_confident_general_chat() -> None:
    result = classify_request(request("Hello, how are you?"))

    assert result.category is TaskCategory.GENERAL_CHAT
    assert result.difficulty is TaskDifficulty.SIMPLE
    assert result.confidence >= 0.65


def test_classification_never_copies_prompt_text_into_explanations() -> None:
    secret = "customer-secret-4f919b"
    result = classify_request(request(f"Summarize {secret}"))
    explanation = " ".join((*result.matched_signals, *result.reason_codes))

    assert secret not in explanation
