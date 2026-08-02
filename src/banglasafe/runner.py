"""Async fan-out over prompts and over judging. Resume-safe, append-only."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from .client import ChatClient
from .dataset import append_jsonl, load_done


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _fan_out(
    items: list,
    work: Callable[[object], Awaitable[dict]],
    *,
    out_path: Path,
    max_concurrency: int,
    label: str,
) -> list[dict]:
    """Run `work` over `items` with bounded concurrency, appending each result as it lands.

    Results are written the moment they arrive rather than at the end, so a crash
    or a Ctrl-C keeps everything already paid for.
    """
    sem = asyncio.Semaphore(max_concurrency)
    lock = asyncio.Lock()
    results: list[dict] = []

    columns = [
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("errors {task.fields[errors]}"),
        TimeRemainingColumn(),
    ]

    with Progress(*columns) as progress:
        task = progress.add_task(label, total=len(items), errors=0)
        errors = 0

        async def one(item) -> None:
            nonlocal errors
            async with sem:
                rec = await work(item)
            async with lock:
                append_jsonl(out_path, rec)
                results.append(rec)
                if rec.get("error"):
                    errors += 1
                progress.update(task, advance=1, errors=errors)

        await asyncio.gather(*(one(i) for i in items))

    return results


async def generate_responses(
    prompts: list[dict],
    client: ChatClient,
    *,
    out_path: Path,
    max_concurrency: int,
) -> list[dict]:
    """Send each prompt to the target model as a single user turn, no system prompt.

    No system prompt is deliberate: it is the consumer-chatbot threat model, and it
    matches how the published numbers were produced.
    """
    model = client.endpoint.model
    done = load_done(out_path)
    pending = [p for p in prompts if f"{model}__{p['prompt_id']}" not in done]

    if not pending:
        return []

    async def work(p: dict) -> dict:
        c = await client.chat([{"role": "user", "content": p["prompt_text"]}])
        return {
            "response_id": f"{model}__{p['prompt_id']}",
            "prompt_id": p["prompt_id"],
            "model": model,
            "category": p["category"],
            "condition": p["condition"],
            "register_tier_target": p.get("register_tier_target"),
            "source": p.get("source"),
            "prompt_text": p["prompt_text"],
            "response_text": c.text,
            "finish_reason": c.finish_reason,
            "prompt_tokens": c.prompt_tokens,
            "completion_tokens": c.completion_tokens,
            "error": c.error,
            "timestamp": _now(),
        }

    return await _fan_out(
        pending, work, out_path=out_path, max_concurrency=max_concurrency,
        label=f"querying {model}",
    )


async def judge_responses(
    responses: Iterable[dict],
    judge,
    *,
    out_path: Path,
    max_concurrency: int,
) -> list[dict]:
    """Label every response with the four-way rubric."""
    done = load_done(out_path)
    pending = [r for r in responses if r.get("response_id") not in done and not r.get("error")]

    if not pending:
        return []

    async def work(r: dict) -> dict:
        return await judge.label(r)

    return await _fan_out(
        pending, work, out_path=out_path, max_concurrency=max_concurrency,
        label=f"judging with {judge.client.endpoint.model}",
    )
