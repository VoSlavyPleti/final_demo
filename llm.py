import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


load_dotenv()


def get_llm(
    max_completion_tokens: int = 300_000,
    thinking: bool = True,
    reasoning_effort: str = "max",
) -> ChatOpenAI:
    model = os.getenv("DEEPSEEK_MODEL")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL")
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_retries=3,
        request_timeout=300.0,
        stream_chunk_timeout=300.0,
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
