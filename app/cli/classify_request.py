import argparse
import json
from dataclasses import asdict

from app.routing.classifier import classify_request
from app.schemas.chat import ChatCompletionRequest, ChatMessage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect the deterministic task classification for a prompt."
    )
    parser.add_argument("prompt")
    parser.add_argument("--system")
    parser.add_argument("--max-tokens", type=int, default=512)
    return parser


def classify_prompt(
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 512,
) -> str:
    messages: list[ChatMessage] = []
    if system:
        messages.append(ChatMessage(role="system", content=system))
    messages.append(ChatMessage(role="user", content=prompt))
    request = ChatCompletionRequest(messages=messages, max_tokens=max_tokens)
    return json.dumps(asdict(classify_request(request)), indent=2)


def main() -> None:
    arguments = build_parser().parse_args()
    print(
        classify_prompt(
            arguments.prompt,
            system=arguments.system,
            max_tokens=arguments.max_tokens,
        )
    )


if __name__ == "__main__":
    main()
