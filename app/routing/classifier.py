import re
from dataclasses import dataclass

from app.routing.taxonomy import TaskCategory, TaskDifficulty
from app.schemas.chat import ChatCompletionRequest

CLASSIFIER_VERSION = "task-rules-v1"


@dataclass(frozen=True, slots=True)
class SignalRule:
    code: str
    pattern: re.Pattern[str]
    weight: int


@dataclass(frozen=True, slots=True)
class TaskClassification:
    category: TaskCategory
    difficulty: TaskDifficulty
    confidence: float
    matched_signals: tuple[str, ...]
    reason_codes: tuple[str, ...]
    classifier_version: str = CLASSIFIER_VERSION


def _rule(code: str, pattern: str, *, weight: int = 1) -> SignalRule:
    return SignalRule(code, re.compile(pattern, re.IGNORECASE), weight)


CATEGORY_RULES: dict[TaskCategory, tuple[SignalRule, ...]] = {
    TaskCategory.CODING: (
        _rule(
            "coding_debugging",
            r"\b(debug|depurar|bug|traceback|stack trace|exception|compile error|"
            r"fix (?:this|the) code)\b",
            weight=2,
        ),
        _rule(
            "coding_construct",
            r"\b(function|funci[oó]n|class|method|api endpoint|sql query|"
            r"regular expression|regex)\b",
        ),
        _rule(
            "coding_language",
            r"\b(python|javascript|typescript|java|rust|golang|c\+\+|postgresql)\b",
        ),
        _rule("coding_fenced_block", r"```"),
    ),
    TaskCategory.REASONING: (
        _rule(
            "reasoning_formal",
            r"\b(prove|proof|demuestra|derive|equation|calculate|calcula|"
            r"logic puzzle|theorem)\b",
            weight=2,
        ),
        _rule(
            "reasoning_analysis",
            r"\b(explain why|analy[sz]e|analiza|compare|compara|trade[- ]offs?|"
            r"step[- ]by[- ]step)\b",
        ),
    ),
    TaskCategory.SUMMARIZATION: (
        _rule(
            "summarization_direct",
            r"\b(summari[sz]e|summary|resume|resumen|tldr|tl;dr|condense|abridge)\b",
            weight=2,
        ),
        _rule(
            "summarization_key_points",
            r"\b(key points|main ideas|main takeaways|bullet points from)\b",
        ),
    ),
    TaskCategory.EXTRACTION: (
        _rule(
            "extraction_direct",
            r"\b(extract|extrae|identify (?:the )?(?:fields|entities)|parse the following)\b",
            weight=2,
        ),
        _rule(
            "extraction_structured_output",
            r"\b(return|output|respond) (?:it )?(?:as|in) (?:valid )?(json|csv|a table)\b",
        ),
    ),
    TaskCategory.GENERAL_CHAT: (
        _rule(
            "general_conversation",
            r"\b(hello|hi|hola|how are you|thank you|thanks|tell me a joke)\b",
            weight=2,
        ),
        _rule(
            "general_creation",
            r"\b(brainstorm|write (?:an? )?(?:email|story|poem)|give me ideas)\b",
            weight=2,
        ),
    ),
}

COMPLEX_DIFFICULTY_PATTERN = re.compile(
    r"\b(debug|root cause|architecture|distributed system|prove|proof|"
    r"optimi[sz]e|multi[- ]step|step[- ]by[- ]step)\b",
    re.IGNORECASE,
)

MEDIUM_DIFFICULTY_PATTERN = re.compile(
    r"\b(several|multiple|with tests|handle errors?|edge cases?|trade[- ]offs?|"
    r"three or more|across sections)\b",
    re.IGNORECASE,
)


def estimate_prompt_tokens(request: ChatCompletionRequest) -> int:
    character_count = sum(
        len(message.role) + len(message.content) + 2 for message in request.messages
    )
    return max(1, (character_count + 3) // 4)


def classify_request(request: ChatCompletionRequest) -> TaskClassification:
    prompt = "\n".join(message.content for message in request.messages)
    scores: dict[TaskCategory, int] = {category: 0 for category in TaskCategory}
    matched: list[str] = []
    for category, rules in CATEGORY_RULES.items():
        for rule in rules:
            if rule.pattern.search(prompt):
                scores[category] += rule.weight
                matched.append(rule.code)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0].value))
    top_category, top_score = ranked[0]
    second_score = ranked[1][1]
    reason_codes: list[str] = []
    if top_score == 0:
        category = TaskCategory.GENERAL_CHAT
        confidence = 0.55
        reason_codes.append("no_category_signal_general_fallback")
    elif top_score == second_score:
        category = TaskCategory.GENERAL_CHAT
        confidence = 0.50
        matched.append("ambiguous_category_tie")
        reason_codes.append("low_confidence_general_fallback")
    else:
        category = top_category
        margin = top_score - second_score
        confidence = min(0.95, 0.62 + 0.10 * min(top_score, 2) + 0.04 * min(margin, 2))
        reason_codes.append(f"category_{category.value}_signal")

    difficulty, difficulty_reason = _classify_difficulty(
        request,
        prompt=prompt,
        category=category,
        low_confidence=confidence < 0.65,
    )
    reason_codes.append(difficulty_reason)
    return TaskClassification(
        category=category,
        difficulty=difficulty,
        confidence=round(confidence, 3),
        matched_signals=tuple(matched),
        reason_codes=tuple(reason_codes),
    )


def _classify_difficulty(
    request: ChatCompletionRequest,
    *,
    prompt: str,
    category: TaskCategory,
    low_confidence: bool,
) -> tuple[TaskDifficulty, str]:
    prompt_tokens = estimate_prompt_tokens(request)
    if any(message.role == "tool" for message in request.messages):
        return TaskDifficulty.COMPLEX, "difficulty_complex_tool_context"
    if (
        prompt_tokens >= 800
        or len(request.messages) >= 8
        or COMPLEX_DIFFICULTY_PATTERN.search(prompt)
    ):
        return TaskDifficulty.COMPLEX, "difficulty_complex_signal"
    if low_confidence:
        return TaskDifficulty.MEDIUM, "difficulty_medium_low_confidence"
    if (
        prompt_tokens >= 200
        or len(request.messages) >= 4
        or MEDIUM_DIFFICULTY_PATTERN.search(prompt)
    ):
        return TaskDifficulty.MEDIUM, "difficulty_medium_signal"
    return TaskDifficulty.SIMPLE, "difficulty_simple_signal"
