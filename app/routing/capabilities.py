from app.schemas.chat import ChatCompletionRequest, TextResponseFormat


def required_capabilities(request: ChatCompletionRequest) -> frozenset[str]:
    capabilities = {"text_generation"}
    if not isinstance(request.response_format, TextResponseFormat):
        capabilities.add("structured_output")
    if request.tools is not None or any(message.role == "tool" for message in request.messages):
        capabilities.add("tool_use")
    return frozenset(capabilities)
