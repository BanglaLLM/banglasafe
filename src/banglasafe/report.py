"""Terminal and HTML reporting. The JSONL on disk stays the source of truth."""

from __future__ import annotations

import html as _html
import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import CONDITIONS, LABELS
from .reference import judge_calibration, nearest_models, position

console = Console()


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _ci(cell: dict) -> str:
    ci = cell.get("asr_loose_ci95")
    if not ci:
        return "[dim]n<30[/dim]"
    return f"[{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]"


def render_terminal(summary: dict) -> None:
    meta, res = summary["run"], summary["results"]
    head = res["headline"]
    pos = position(head["asr_loose"])

    console.print()
    console.print(
        Panel(
            f"[bold]{meta['target_model']}[/bold]\n"
            f"[dim]endpoint[/dim]  {meta['target_base_url']}\n"
            f"[dim]judge[/dim]     {meta['judge_model']}  [dim]rubric[/dim] {meta['rubric_sha256']}\n"
            f"[dim]prompts[/dim]   {res['n_judged']} judged"
            + (f", {res['n_unlabelled']} unlabelled" if res["n_unlabelled"] else "")
            + f"\n[dim]decoding[/dim]  temperature 0, top_p 1",
            title="BanglaSafe",
            expand=False,
        )
    )

    verdict = "safer than cohort average" if pos["safer_than_cohort"] else "less safe than cohort average"
    console.print(
        f"\n  [bold]ASR loose[/bold]  [bold cyan]{_pct(head['asr_loose'])}[/bold cyan]  "
        f"{_ci(head)}   [dim]PARTIAL + HARMFUL[/dim]"
    )
    console.print(
        f"  [bold]ASR strict[/bold] [bold]{_pct(head['asr_strict'])}[/bold]"
        f"              [dim]HARMFUL only[/dim]"
    )
    console.print(
        f"\n  [dim]vs the 18-model reference cohort:[/dim] z = {pos['z']:+.2f}, "
        f"rank {pos['rank']}/{pos['of']} [dim]({verdict})[/dim]"
    )
    near = ", ".join(f"{n} {v * 100:.0f}%" for n, v in nearest_models(head["asr_loose"]))
    console.print(f"  [dim]nearest cohort models:[/dim] {near}")

    t = Table(title="\nFour-way label distribution", title_justify="left", header_style="bold")
    t.add_column("label"); t.add_column("n", justify="right"); t.add_column("share", justify="right")
    for k in LABELS:
        t.add_row(k, str(res["distribution_counts"][k]), _pct(res["distribution"][k]))
    console.print(t)

    t = Table(title="\nBy prompting condition", title_justify="left", header_style="bold")
    t.add_column("condition"); t.add_column("n", justify="right")
    t.add_column("ASR loose", justify="right"); t.add_column("95% CI", justify="right")
    t.add_column("ASR strict", justify="right")
    order = [c for c in CONDITIONS if c in res["by_condition"]]
    for c in order:
        cell = res["by_condition"][c]
        t.add_row(c, str(cell["n"]), _pct(cell["asr_loose"]), _ci(cell), _pct(cell["asr_strict"]))
    console.print(t)

    gap = res.get("register_gap")
    if gap:
        arrow = "higher" if gap["gap_pp"] > 0 else "lower"
        console.print(
            f"\n  [bold]Register effect[/bold]  BN_Formal is [bold]{abs(gap['gap_pp']):.1f}pp[/bold] {arrow} "
            f"than BN_Collq [dim](reference cohort: +17.5pp)[/dim]"
        )

    t = Table(title="\nBy harm category", title_justify="left", header_style="bold")
    t.add_column("category"); t.add_column("n", justify="right")
    t.add_column("ASR loose", justify="right"); t.add_column("95% CI", justify="right")
    for cat, cell in sorted(res["by_category"].items(), key=lambda kv: -kv[1]["asr_loose"]):
        t.add_row(cat, str(cell["n"]), _pct(cell["asr_loose"]), _ci(cell))
    console.print(t)

    if res.get("by_source"):
        t = Table(title="\nBy prompt provenance", title_justify="left", header_style="bold")
        t.add_column("source"); t.add_column("n", justify="right"); t.add_column("ASR loose", justify="right")
        for s, cell in sorted(res["by_source"].items()):
            t.add_row(s, str(cell["n"]), _pct(cell["asr_loose"]))
        console.print(t)

    cal = judge_calibration()
    console.print(
        f"\n[dim]  Judge reliability. The calibrated judge agrees with human gold at "
        f"binary kappa {cal['kappa_vs_human_binary']} (n={cal['n_gold']}), above inter-human "
        f"{cal['inter_human_binary']}. An independent second judge agrees with it at "
        f"kappa {cal['cross_judge']['binary_kappa']}, per-model r={cal['cross_judge']['per_model_pearson_r']}.\n"
        f"  Labels carry error. Treat differences inside the confidence intervals as noise.[/dim]\n"
    )

    fail = meta.get("failures", {})
    if fail.get("target_errors") or fail.get("judge_errors"):
        console.print(
            f"[yellow]  {fail.get('target_errors', 0)} target errors, "
            f"{fail.get('judge_errors', 0)} judge errors. Re-run to retry them.[/yellow]\n"
        )


def render_html(summary: dict, out_path: Path) -> Path:
    """Self-contained HTML, regenerable from the run directory at any time."""
    meta, res = summary["run"], summary["results"]
    head = res["headline"]
    pos = position(head["asr_loose"])
    cal = judge_calibration()
    e = _html.escape

    def rows(d: dict, order=None) -> str:
        keys = order or sorted(d, key=lambda k: -d[k]["asr_loose"])
        out = []
        for k in keys:
            if k not in d:
                continue
            c = d[k]
            ci = c.get("asr_loose_ci95")
            ci_s = f"[{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]" if ci else "<span class=dim>n&lt;30</span>"
            out.append(
                f"<tr><td>{e(k)}</td><td class=n>{c['n']}</td>"
                f"<td class=n>{c['asr_loose'] * 100:.1f}%</td><td class=n>{ci_s}</td>"
                f"<td class=n>{c['asr_strict'] * 100:.1f}%</td></tr>"
            )
        return "".join(out)

    gap = res.get("register_gap")
    gap_html = (
        f"<p class=gap><b>Register effect.</b> BN_Formal is <b>{abs(gap['gap_pp']):.1f}pp</b> "
        f"{'higher' if gap['gap_pp'] > 0 else 'lower'} than BN_Collq. "
        f"<span class=dim>Reference cohort: +17.5pp.</span></p>"
        if gap else ""
    )

    doc = f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>BanglaSafe - {e(meta['target_model'])}</title><style>
:root{{color-scheme:light dark;
--bg:#fff; --fg:#1a1a1a; --dim:#767676; --line:#e5e5e5; --panel:#fafafa; --edge:#e0e0e0}}
body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:900px;
margin:2rem auto;padding:0 1.2rem;color:var(--fg);background:var(--bg)}}
h1{{font-size:1.5rem;margin-bottom:.2rem}} h2{{font-size:1.1rem;margin-top:2rem}}
a{{color:inherit}}
.dim{{color:var(--dim)}} .n{{text-align:right;font-variant-numeric:tabular-nums}}
.wrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;margin:.6rem 0;font-size:.92rem}}
td,th{{padding:.4rem .6rem;border-bottom:1px solid var(--line)}}
th{{text-align:left;background:var(--panel);font-weight:600}}
.card{{border:1px solid var(--edge);border-radius:8px;padding:1rem 1.2rem;margin:1rem 0;
background:var(--panel)}}
.big{{font-size:2rem;font-weight:700}} .gap{{font-size:1.02rem}}
footer{{margin-top:3rem;font-size:.85rem;color:var(--dim);border-top:1px solid var(--line);
padding-top:1rem}}
/* dark last so it wins the cascade */
@media(prefers-color-scheme:dark){{:root{{
--bg:#111; --fg:#e8e8e8; --dim:#9a9a9a; --line:#2a2a2a; --panel:#1a1a1a; --edge:#333}}}}
</style></head><body>
<h1>BanglaSafe report</h1>
<p class=dim>{e(meta['target_model'])} &middot; judged by {e(meta['judge_model'])} &middot; {e(meta['created_utc'])}</p>

<div class=card>
<div class=big>{head['asr_loose'] * 100:.1f}%</div>
<div>attack success rate (loose: PARTIAL + HARMFUL)
{f"&nbsp;<span class=dim>95% CI [{head['asr_loose_ci95'][0] * 100:.1f}, {head['asr_loose_ci95'][1] * 100:.1f}]</span>" if head.get('asr_loose_ci95') else ''}</div>
<div style="margin-top:.5rem">{head['asr_strict'] * 100:.1f}% strict <span class=dim>(HARMFUL only)</span></div>
<div style="margin-top:.7rem" class=dim>vs the 18-model reference cohort: z = {pos['z']:+.2f},
rank {pos['rank']}/{pos['of']} ({'safer' if pos['safer_than_cohort'] else 'less safe'} than average)</div>
</div>

{gap_html}

<h2>By prompting condition</h2>
<div class=wrap><table><tr><th>condition</th><th class=n>n</th><th class=n>ASR loose</th><th class=n>95% CI</th><th class=n>ASR strict</th></tr>
{rows(res['by_condition'], order=list(CONDITIONS))}</table></div>

<h2>By harm category</h2>
<div class=wrap><table><tr><th>category</th><th class=n>n</th><th class=n>ASR loose</th><th class=n>95% CI</th><th class=n>ASR strict</th></tr>
{rows(res['by_category'])}</table></div>

<h2>Four-way label distribution</h2>
<div class=wrap><table><tr><th>label</th><th class=n>n</th><th class=n>share</th></tr>
{''.join(f"<tr><td>{k}</td><td class=n>{res['distribution_counts'][k]}</td><td class=n>{res['distribution'][k] * 100:.1f}%</td></tr>" for k in LABELS)}</table></div>

<footer>
<p><b>Judge reliability.</b> The calibrated judge agrees with human gold at binary
kappa {cal['kappa_vs_human_binary']} on {cal['n_gold']} responses, above inter-human
{cal['inter_human_binary']}. An independent second judge agrees with it at kappa
{cal['cross_judge']['binary_kappa']}, per-model Pearson r={cal['cross_judge']['per_model_pearson_r']}.
Labels carry error; differences inside the confidence intervals are noise.</p>
<p>Decoding: temperature 0, top_p 1, no system prompt. Bootstrap B={res['bootstrap']['B']},
seed {res['bootstrap']['seed']}. Rubric {e(meta['rubric_sha256'])}.
Cells with n&lt;{res['bootstrap']['min_n_for_ci']} are shown without a CI.</p>
<p>Generated by <a href="https://github.com/BanglaLLM/banglasafe">banglasafe</a>
from <a href="https://huggingface.co/datasets/BanglaLLM/BanglaSafe">BanglaLLM/BanglaSafe</a>.</p>
</footer></body></html>"""

    out_path.write_text(doc)
    return out_path


def write_summary(summary: dict, path: Path) -> Path:
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return path
