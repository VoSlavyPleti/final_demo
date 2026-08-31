"""Content logging to the local console, separate from safe audit traces."""

import json
import os
import re
import threading
from datetime import datetime

from gigachat.exceptions import ResponseError
from langchain_core.callbacks import BaseCallbackHandler


class ConsoleLogHandler(BaseCallbackHandler):
    """Print model and tool activity, including inherited subagent callbacks.

    This intentionally exposes business content to the execution console. Never
    attach it to the metadata-only trace sink or dump serialized client config.
    """

    run_inline = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        values = {value for key, value in os.environ.items() if value and any(
                marker in key.upper()
                for marker in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIALS", "API_KEY")
            )}
        self._secrets = sorted(
            values | {json.dumps(value, ensure_ascii=False)[1:-1] for value in values},
            key=len, reverse=True,
        )

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(
            r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
            "[REDACTED PRIVATE KEY]", text, flags=re.DOTALL,
        )
        text = re.sub(r"(?i)\b(Bearer|Basic)\s+[\w.+/~=-]+", r"\1 [REDACTED]", text)
        # Prevent provider/tool text from injecting terminal control sequences.
        return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)

    def _emit(self, event, content, *, run_id, parent_run_id=None) -> None:
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2, default=str)
        header = (
            f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] "
            f"[agent:{event}] run={run_id} parent={parent_run_id or '-'}"
        )
        with self._lock:
            print(self._redact(f"{header}\n{content}\n"), flush=True)

    @staticmethod
    def _message(message) -> dict:
        result = {"role": message.type, "content": message.content}
        for field in ("name", "tool_call_id", "tool_calls", "invalid_tool_calls"):
            value = getattr(message, field, None)
            if value:
                result[field] = value
        # Only content explicitly returned by the provider; no raw HTTP metadata.
        reasoning = message.additional_kwargs.get("reasoning_content")
        if reasoning:
            result["reasoning_content"] = reasoning
        return result

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, **kwargs):
        self._emit("model_input", [
            [self._message(message) for message in batch] for batch in messages
        ], run_id=run_id, parent_run_id=parent_run_id)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs):
        self._emit("model_input", prompts, run_id=run_id, parent_run_id=parent_run_id)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        outputs = []
        for batch in response.generations:
            for generation in batch:
                message = getattr(generation, "message", None)
                outputs.append(self._message(message) if message is not None else generation.text)
        self._emit("model_output", outputs, run_id=run_id, parent_run_id=parent_run_id)

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs):
        self._emit("tool_start", {
            "name": (serialized or {}).get("name", "tool"),
            "input": kwargs.get("inputs") if kwargs.get("inputs") is not None else input_str,
        }, run_id=run_id, parent_run_id=parent_run_id)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        if hasattr(output, "content"):
            output = self._message(output)
        self._emit("tool_output", output, run_id=run_id, parent_run_id=parent_run_id)

    def _error(self, event, error, run_id, parent_run_id):
        details = {"error_type": type(error).__name__}
        if isinstance(error, ResponseError):
            details["status_code"] = error.status_code
        else:
            details["message"] = str(error)
        self._emit(event, details, run_id=run_id, parent_run_id=parent_run_id)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._error("model_error", error, run_id, parent_run_id)

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._error("tool_error", error, run_id, parent_run_id)
