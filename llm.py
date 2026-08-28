import os

import httpx
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


load_dotenv()


def get_llm(
    max_completion_tokens: int = 300_000,
    thinking: bool = True,
    reasoning_effort: str = "high",
    request_timeout: float = 1800.0,
    max_retries: int = 3,
) -> ChatOpenAI:
    model = os.getenv("DEEPSEEK_MODEL")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL")
    # DeepSeek occasionally terminates a reused HTTP/1.1 chunked stream before
    # [DONE]. Long reasoning calls use a fresh connection to avoid inheriting
    # an upstream keep-alive lifetime from the preceding short tool turns.
    http_client = httpx.Client(
        limits=httpx.Limits(
            max_connections=20,
            max_keepalive_connections=0,
        ),
        timeout=httpx.Timeout(request_timeout, connect=min(30.0, request_timeout)),
    )
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=max_retries,
        # Full-contract reasoning may legitimately run longer than the default
        # compatible-endpoint read timeout before emitting another chunk.
        request_timeout=request_timeout,
        stream_chunk_timeout=request_timeout,
        temperature=0.0,
        streaming=thinking,
        max_completion_tokens=max_completion_tokens,
        reasoning_effort=reasoning_effort if thinking else None,
        extra_body={
            "thinking": {"type": "enabled" if thinking else "disabled"},
            "max_tokens": max_completion_tokens,
            "reasoning_effort": reasoning_effort,
        },
    )


def close_llm(model: ChatOpenAI | None) -> None:
    """Close the explicitly-created sync HTTP client owned by ``get_llm``."""

    if model is None:
        return
    client = getattr(model, "http_client", None)
    close = getattr(client, "close", None)
    if callable(close):
        close()


__all__ = [
    "close_llm",
    "get_llm",
]
