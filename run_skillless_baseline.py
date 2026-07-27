from __future__ import annotations

import argparse
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

import main


SYSTEM_PROMPT = """
Ты — агент анализа договоров. Исходные документы находятся в
`/inputs/contract.txt` и `/inputs/matrix.json`.
""".strip()


USER_PROMPT = """
Сопоставь положения договора с положениями банковской матрицы по юридическому
смыслу в формате many-to-many.

В ответе отрази:
- номер и текст каждого смыслового пункта договора;
- номера и тексты найденных аналогов в матрице;
- статус сопоставленной группы: aligned или deviation;
- краткое объяснение статуса;
- отдельно пункты договора, не имеющие юридического аналога в матрице
  (extra_in_contract);
- отдельно применимые пункты матрицы, не имеющие аналога в договоре
  (missing_in_contract).

Проверь, что рассмотрены все смысловые нумерованные пункты договора и все
применимые рабочие пункты матрицы.
""".strip()


def _message_text(message: object) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def main_cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--workspace-record", type=Path, required=True)
    args = parser.parse_args()

    contract = args.contract.resolve()
    matrix = args.matrix.resolve()
    response = args.response.resolve()
    workspace_record = args.workspace_record.resolve()
    response.parent.mkdir(parents=True, exist_ok=True)
    workspace_record.parent.mkdir(parents=True, exist_ok=True)

    workspace = Path(tempfile.mkdtemp(prefix="contract-skillless-"))
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "outputs").mkdir(parents=True)
    shutil.copyfile(contract, workspace / "inputs" / "contract.txt")
    shutil.copyfile(matrix, workspace / "inputs" / "matrix.json")
    workspace_record.write_text(str(workspace), encoding="utf-8")

    agent = main.create_deep_agent(
        name="skillless-contract-review-agent",
        model=main.get_llm(),
        system_prompt=SYSTEM_PROMPT,
        backend=main.build_backend(workspace),
        checkpointer=MemorySaver(),
    )

    started = time.perf_counter()
    result = agent.invoke(
        {"messages": [{"role": "user", "content": USER_PROMPT}]},
        config={
            "configurable": {"thread_id": uuid.uuid4().hex},
            "recursion_limit": 10_000,
            "callbacks": [main.CompactTraceHandler()],
        },
    )
    messages = result.get("messages", [])
    final_text = _message_text(messages[-1]) if messages else ""
    elapsed = time.perf_counter() - started
    response.write_text(
        f"Elapsed seconds: {elapsed:.1f}\n\n{final_text}",
        encoding="utf-8",
    )
    print(f"[skillless] finished in {elapsed:.1f}s", flush=True)
    print(response, flush=True)
    print(workspace, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
