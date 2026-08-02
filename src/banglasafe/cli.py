"""banglasafe - evaluate any OpenAI-compatible model on the BanglaSafe benchmark."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .client import ChatClient
from .config import CALIBRATED_JUDGES, CONDITIONS, HF_DATASET, resolve
from .dataset import load_prompts, read_jsonl
from .judge import Judge, rubric_sha256
from .report import render_html, render_terminal, write_summary
from .reference import cohort_models, judge_calibration
from .runner import generate_responses, judge_responses
from .scoring import score

app = typer.Typer(
    name="banglasafe",
    help="Evaluate any OpenAI-compatible model on the BanglaSafe Bengali safety benchmark.",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()


def _judge_help() -> str:
    lines = ["\n[bold]Judges with published calibration on this benchmark:[/bold]"]
    for name, info in CALIBRATED_JUDGES.items():
        lines.append(f"  [cyan]{name}[/cyan]\n    {info['note']}")
    lines.append(
        "\nAny OpenAI-compatible model works as a judge, but only the models above "
        "have a measured agreement with human labels on BanglaSafe. Using an "
        "uncalibrated judge means your numbers are not comparable to published ones."
    )
    return "\n".join(lines)


@app.command()
def run(
    model: Annotated[str, typer.Option("--model", "-m", help="Model id as your endpoint names it.")],
    judge_model: Annotated[
        Optional[str], typer.Option("--judge-model", help="Judge model id. Required, no default.")
    ] = None,
    base_url: Annotated[
        Optional[str],
        typer.Option(envvar=["BANGLASAFE_BASE_URL", "OPENAI_BASE_URL"],
                     help="OpenAI-compatible base, e.g. http://localhost:8000/v1"),
    ] = None,
    api_key: Annotated[
        Optional[str], typer.Option(envvar=["BANGLASAFE_API_KEY", "OPENAI_API_KEY"])
    ] = None,
    judge_base_url: Annotated[
        Optional[str], typer.Option(envvar="BANGLASAFE_JUDGE_BASE_URL")
    ] = None,
    judge_api_key: Annotated[
        Optional[str], typer.Option(envvar="BANGLASAFE_JUDGE_API_KEY")
    ] = None,
    max_concurrency: Annotated[int, typer.Option(help="Concurrent in-flight requests.")] = 8,
    max_tokens: Annotated[int, typer.Option(help="Max completion tokens from the target.")] = 4096,
    limit: Annotated[
        Optional[int], typer.Option(help="Evaluate N harm instances (all 5 conditions each), spread across categories.")
    ] = None,
    condition: Annotated[Optional[list[str]], typer.Option(help="Restrict to conditions. Repeatable.")] = None,
    category: Annotated[Optional[list[str]], typer.Option(help="Restrict to harm categories. Repeatable.")] = None,
    output_dir: Annotated[Path, typer.Option(help="Where run artifacts are written.")] = Path("results"),
    config: Annotated[Optional[Path], typer.Option(help="YAML config. CLI flags override it.")] = None,
    html: Annotated[bool, typer.Option(help="Also write an HTML report.")] = True,
) -> None:
    """Query the model, judge every response, score, and report."""
    if not judge_model:
        console.print("\n[red]--judge-model is required.[/red] The judge determines your numbers, so it is an explicit choice.")
        console.print(_judge_help())
        console.print("\n[dim]Example:[/dim]  banglasafe run -m my-model --judge-model anthropic/claude-opus-4-7\n")
        raise typer.Exit(2)

    if judge_model not in CALIBRATED_JUDGES:
        console.print(
            f"[yellow]Note:[/yellow] '{judge_model}' has no published calibration on BanglaSafe. "
            "Results will not be comparable to published numbers.\n"
        )

    s = resolve(
        model=model, base_url=base_url, api_key=api_key,
        judge_model=judge_model, judge_base_url=judge_base_url, judge_api_key=judge_api_key,
        max_tokens=max_tokens, max_concurrency=max_concurrency, output_dir=output_dir,
        limit=limit, conditions=condition, categories=category, config_path=config,
    )

    run_id = f"{model.replace('/', '_')}__{int(datetime.now(timezone.utc).timestamp())}"
    run_dir = s.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    asyncio.run(_execute(s, run_dir, write_html=html))


async def _execute(s, run_dir: Path, *, write_html: bool) -> None:
    console.print(f"\n[dim]loading prompts from[/dim] {HF_DATASET}")
    prompts = load_prompts(conditions=s.conditions, categories=s.categories, limit=s.limit)
    console.print(f"[dim]{len(prompts)} prompts[/dim]  ->  {run_dir}\n")

    target = ChatClient(s.target)
    judge_client = ChatClient(s.judge)

    err = await target.probe()
    if err:
        console.print(f"[red]Target endpoint unreachable:[/red] {err}")
        console.print(f"[dim]  model    {s.target.model}\n  base_url {s.target.base_url}[/dim]")
        await target.close(); await judge_client.close()
        raise typer.Exit(1)

    err = await judge_client.probe()
    if err:
        console.print(f"[red]Judge endpoint unreachable:[/red] {err}")
        console.print(f"[dim]  model    {s.judge.model}\n  base_url {s.judge.base_url}[/dim]")
        await target.close(); await judge_client.close()
        raise typer.Exit(1)

    resp_path = run_dir / "responses.jsonl"
    label_path = run_dir / "labels.jsonl"

    await generate_responses(prompts, target, out_path=resp_path, max_concurrency=s.max_concurrency)
    responses = read_jsonl(resp_path)

    judge = Judge(judge_client)
    await judge_responses(responses, judge, out_path=label_path, max_concurrency=s.max_concurrency)
    labels = read_jsonl(label_path)

    await target.close()
    await judge_client.close()

    summary = _summarize(s, run_dir, responses, labels)
    write_summary(summary, run_dir / "summary.json")
    render_terminal(summary)
    if write_html:
        p = render_html(summary, run_dir / "report.html")
        console.print(f"[dim]  html report:[/dim] {p}\n")


def _summarize(s, run_dir: Path, responses: list[dict], labels: list[dict]) -> dict:
    results = score(labels)
    return {
        "schema_version": "1.0",
        "banglasafe_version": __version__,
        "run": {
            "run_id": run_dir.name,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "target_model": s.target.model,
            "target_base_url": s.target.base_url,
            "judge_model": s.judge.model,
            "rubric_sha256": rubric_sha256(),
            "dataset": HF_DATASET,
            "decoding": {"temperature": 0.0, "top_p": 1.0, "max_tokens": s.target.max_tokens,
                         "system_prompt": None},
            "n_prompts": len(responses),
            "failures": {
                "target_errors": sum(1 for r in responses if r.get("error")),
                "judge_errors": sum(1 for r in labels if r.get("error")),
                "truncated": sum(1 for r in responses if r.get("finish_reason") == "length"),
            },
            "tokens": {
                "prompt": sum(r.get("prompt_tokens") or 0 for r in responses),
                "completion": sum(r.get("completion_tokens") or 0 for r in responses),
            },
        },
        "results": results,
    }


@app.command("score")
def score_cmd(
    run_dir: Annotated[Path, typer.Argument(help="A run directory containing labels.jsonl.")],
    html: Annotated[bool, typer.Option(help="Also write an HTML report.")] = False,
) -> None:
    """Re-score an existing run. No network calls."""
    label_path = run_dir / "labels.jsonl"
    if not label_path.exists():
        console.print(f"[red]No labels.jsonl in {run_dir}[/red]")
        raise typer.Exit(1)

    summary_path = run_dir / "summary.json"
    prev = json.loads(summary_path.read_text()) if summary_path.exists() else {"run": {}}
    labels = read_jsonl(label_path)

    summary = {
        "schema_version": "1.0",
        "banglasafe_version": __version__,
        "run": {**prev.get("run", {}), "rescored_utc": datetime.now(timezone.utc).isoformat()},
        "results": score(labels),
    }
    summary["run"].setdefault("target_model", "unknown")
    summary["run"].setdefault("target_base_url", "-")
    summary["run"].setdefault("judge_model", "unknown")
    summary["run"].setdefault("rubric_sha256", rubric_sha256())
    summary["run"].setdefault("created_utc", "-")

    write_summary(summary, summary_path)
    render_terminal(summary)
    if html:
        console.print(f"[dim]  html report:[/dim] {render_html(summary, run_dir / 'report.html')}\n")


@app.command()
def report(
    run_dir: Annotated[Path, typer.Argument(help="A run directory containing summary.json.")],
    html_out: Annotated[Optional[Path], typer.Option("--html", help="Where to write the HTML.")] = None,
) -> None:
    """Re-render a finished run. The JSONL on disk stays the source of truth."""
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        console.print(f"[red]No summary.json in {run_dir}[/red]  [dim]run `banglasafe score` first[/dim]")
        raise typer.Exit(1)
    summary = json.loads(summary_path.read_text())
    render_terminal(summary)
    p = render_html(summary, html_out or run_dir / "report.html")
    console.print(f"[dim]  html report:[/dim] {p}\n")


@app.command()
def compare(
    run_dirs: Annotated[list[Path], typer.Argument(help="Two or more run directories.")],
) -> None:
    """Rank several runs side by side."""
    rows = []
    for d in run_dirs:
        p = d / "summary.json"
        if not p.exists():
            console.print(f"[yellow]skipping {d}: no summary.json[/yellow]")
            continue
        s = json.loads(p.read_text())
        rows.append((s["run"].get("target_model", d.name), s["results"]))

    if not rows:
        raise typer.Exit(1)

    t = Table(title="\nBanglaSafe comparison", title_justify="left", header_style="bold")
    t.add_column("model"); t.add_column("n", justify="right")
    t.add_column("ASR loose", justify="right"); t.add_column("ASR strict", justify="right")
    for c in CONDITIONS:
        t.add_column(c.replace("_", "\n"), justify="right")

    for name, res in sorted(rows, key=lambda r: r[1]["headline"]["asr_loose"]):
        cells = [f"{res['by_condition'][c]['asr_loose'] * 100:.1f}" if c in res["by_condition"] else "-"
                 for c in CONDITIONS]
        t.add_row(name, str(res["n_judged"]),
                  f"{res['headline']['asr_loose'] * 100:.1f}%",
                  f"{res['headline']['asr_strict'] * 100:.1f}%", *cells)
    console.print(t)
    console.print("[dim]  sorted safest first. Values are ASR loose per condition (%).[/dim]\n")


@app.command()
def judges() -> None:
    """Show judge options and their measured agreement with human labels."""
    cal = judge_calibration()
    console.print(_judge_help())
    console.print(
        f"\n[bold]Calibration of the published judge[/bold]\n"
        f"  vs human gold, binary   kappa {cal['kappa_vs_human_binary']}  (n={cal['n_gold']})\n"
        f"  vs human gold, four-way kappa {cal['kappa_vs_human_4way']}\n"
        f"  inter-human, binary     kappa {cal['inter_human_binary']}\n"
        f"\n[bold]Cross-judge agreement[/bold] (independent {cal['cross_judge']['judge']})\n"
        f"  binary kappa            {cal['cross_judge']['binary_kappa']}\n"
        f"  per-model Pearson r     {cal['cross_judge']['per_model_pearson_r']}\n"
        f"  per-model Spearman rho  {cal['cross_judge']['per_model_spearman_rho']}\n"
        f"  n                       {cal['cross_judge']['n']}\n"
    )


@app.command()
def cohort() -> None:
    """Show the 18-model reference cohort your score is positioned against."""
    ms = cohort_models()
    t = Table(title="\nBanglaSafe reference cohort", title_justify="left", header_style="bold")
    t.add_column("model"); t.add_column("ASR loose", justify="right"); t.add_column("ASR strict", justify="right")
    for c in CONDITIONS:
        t.add_column(c.replace("_", "\n"), justify="right")
    for name, m in sorted(ms.items(), key=lambda kv: -kv[1]["asr_loose"]):
        t.add_row(name, f"{m['asr_loose'] * 100:.1f}%", f"{m['asr_strict'] * 100:.1f}%",
                  *[f"{m['by_condition'].get(c, 0) * 100:.1f}" for c in CONDITIONS])
    console.print(t)
    console.print("[dim]  Judged by the calibrated four-way judge over 879 prompts each.[/dim]\n")


@app.command()
def info() -> None:
    """Dataset, rubric, and version details."""
    console.print(
        f"\n[bold]banglasafe[/bold] {__version__}\n"
        f"  dataset  {HF_DATASET}  [dim]https://huggingface.co/datasets/{HF_DATASET}[/dim]\n"
        f"  rubric   sha256 {rubric_sha256()}  [dim]four-way REFUSE/POLICY/PARTIAL/HARMFUL[/dim]\n"
        f"  prompts  879 across 17 harm categories and 5 prompting conditions\n"
        f"  decoding temperature 0, top_p 1, no system prompt\n"
        f"\n  [dim]Any endpoint speaking /v1/chat/completions works: vLLM, SGLang, LightLLM,\n"
        f"  Ollama, llama.cpp, TGI, LM Studio, a LiteLLM proxy, or a hosted API.[/dim]\n"
    )


if __name__ == "__main__":
    app()
