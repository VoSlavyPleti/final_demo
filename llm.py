import os

import httpx
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


load_dotenv()


def get_llm(
    max_completion_tokens: int = 300_000,
    thinking: bool = True,
    reasoning_effort: str = "high",
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
        timeout=httpx.Timeout(1800.0, connect=30.0),
    )
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=3,
        # Full-contract reasoning may legitimately run longer than the default
        # compatible-endpoint read timeout before emitting another chunk.
        request_timeout=1800.0,
        stream_chunk_timeout=1800.0,
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


__all__ = [
    "get_llm",
]
