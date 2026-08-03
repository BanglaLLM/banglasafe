
## CHANGE LOG — 2026-08-03 evening (5): leaderboard page built

**New: `docs/leaderboard.html` + `docs/leaderboard_data.json` + `scripts/leaderboard_data.py`.**
Committed locally as `9ca8192`, NOT pushed. Naymul asked for a JailbreakBench-style leaderboard in
our own UI, linked from the hero with an icon.

### Data generator — `scripts/leaderboard_data.py`

Emits one row per model per judge: overall ASR_loose with a bootstrap CI, ASR_strict, the five
per-condition rates, and the register gap. Reads the same two label files the paper uses
(`data/claude_judge_bulk_15522.jsonl`, `data/gemini_judge_full_matched.jsonl`) and imports the model
grouping and display names verbatim from `scripts/claude_register_table.py` so the leaderboard
cannot drift from Table 15. Bootstrap B=10,000, seed 20260521, per the locked methodology.

Self-validating: asserts 18 models, per-model n summing to the judge total, CI bracketing the point
estimate, strict <= loose, and no missing condition cells. Output 13.5 KB.

**Bug I introduced and caught in the same pass:** `gap` was first computed as
`round(BN_Formal,1) - round(BN_Collq,1)`, i.e. a difference of already-rounded rates. That produced
a median register gap of **+17.5pp**, which disagrees with the paper's **+17.6pp**. The unrounded
median is 17.5983. Fixed to keep raw rates for the subtraction and round once. This is the same
double-rounding class of error I flagged in the paper audit, so it is now a comment in the script.

### Independent verification of the payload

Recomputed six values per model (overall ASR, n, BN_Formal, BN_Collq, EN_Direct, gap) directly from
the raw JSONL with **no shared code** with the generator: **108 checks, 0 mismatches**. Cross-checked
against the four-model excerpt already on the index page: Claude-Haiku-4.5 7.5/37.9,
Grok-4.3 8.1/32.8, Gemini-2.5-Flash 14.5/87.9, Gemma-4-26B 18.5/90.8 — all identical.

Headline figures the page rests on, all recomputed: cohort ASR 53.6% (Claude) / 50.0% (Gemini),
17/18 models with a positive register gap, median +17.6pp, safest Llama-3.2-3B at 3.9%, least safe
Mistral-Medium-3 at 89.5%, widest gap Qwen3-30B at +34.9, sole negative gap TigerLLM-1B at -0.1.

### Page

Same cream/dark theme and nav as index.html, `Leaderboard` marked as the current page. Ranked
**lowest ASR first** so rank 1 is the safest model, not the most jailbroken. Twelve columns:
rank, model, vendor, open/closed chip, ASR with an inline bar and bootstrap CI, strict, the five
conditions, and the gap. The most permissive register in each row is shaded. Controls: judge toggle
(Claude Opus 4.7 / Gemini 3.1 Pro), scope filter (all / open-weight / closed-source), free-text
search over model and vendor, and click-to-sort on every column with direction toggle. Footnotes
explain ASR vs strict, what the gap isolates, why two judges are shown, and the 28 responses Gemini
declined. A submission block shows the `uvx banglasafe run` command.

`index.html` gains a `Leaderboard` hero button with a trophy icon plus a nav link.

### Two real bugs found by verification, not by looking

1. **Gap column was unreachable.** The table's intrinsic width was 1129px inside a 1076px container,
   so the rightmost column — the one the whole paper is about — sat outside the scroll viewport.
   Fixed by widening `--maxw` 1120 -> 1200 and tightening cell padding 11px -> 9px. Table now 1156px
   in a 1156px container, no horizontal scroll at full width.
2. **The sticky header was silently dead.** `.tablewrap{overflow-x:auto}` makes the wrapper the
   sticky containing block, so `thead th{position:sticky}` pinned to a box that never scrolls
   vertically. Measured: header top at -156px when scrolled 700px, i.e. it scrolled straight off.
   Fixed by only applying `overflow-x:auto` below 1200px, where the table genuinely needs to scroll
   and sticky cannot work anyway. Verified after: header pins at 54px and `elementFromPoint` in the
   header band returns a `TH`, so it paints over the rows rather than under them.
   (My first attempt to measure this was also wrong — I read the `<tr>` box, but sticky is on the
   `<th>`, so the tr rect follows normal flow and always looks unpinned.)

### Verification

| check | result |
|---|---|
| payload vs raw JSONL, 6 values x 18 models | 108/108 match |
| default sort | Llama-3.2-3B 3.9% first, Mistral-Medium-3 89.5% last, ranks 1..18 |
| judge toggle | swaps to Gemini, n 15,822 -> 15,794, cohort 53.6% -> 50.0% |
| scope filter | closed = 6 models all chipped closed, open = 12, ranks restart at 1 |
| search "google" | 4 rows (Gemini-2.5-Flash + 3 Gemma), matches vendor not just name |
| sort by gap desc | Qwen3-30B +34.9, Mistral-Medium-3 +27.1, GPT-4.1-mini +26.1 |
| sort by model asc | Claude-Haiku-4.5 first, TigerLLM-9B-it last |
| WCAG contrast | 0 failures cream, 0 dark (fixed `.chip.closed` from 4.08:1 with a `--chiphot` token) |
| sticky header when scrolled | pinned at 54px, paints over rows |
| 390px | nav 55px, table scrolls in wrapper, no clipped cell, filters work, 0 page overflow |

**Not pushed.** Naymul's earlier "push it" covered the previous batch; this is new work and he has
not seen it yet. Serving locally at http://127.0.0.1:8931/leaderboard.html for review.
