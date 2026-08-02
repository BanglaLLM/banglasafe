"""Position a score against the published 18-model reference cohort.

A bare "47.2%" means nothing on its own. Against the cohort it becomes
"safer than average, rank 11 of 19, worst in formal Bengali".
"""

from __future__ import annotations

import json
from functools import lru_cache

from .config import REFERENCE_PATH


@lru_cache(maxsize=1)
def cohort() -> dict:
    return json.loads(REFERENCE_PATH.read_text())


def position(asr_loose: float) -> dict:
    """Z-score and rank against the cohort. Rank 1 is safest."""
    c = cohort()
    agg = c["aggregate"]["asr_loose"]
    mean, sd = agg["mean"], agg["sd"]

    others = sorted(m["asr_loose"] for m in c["models"].values())
    n_safer = sum(1 for v in others if v < asr_loose)

    return {
        "z": round((asr_loose - mean) / sd, 2) if sd else 0.0,
        "rank": n_safer + 1,
        "of": len(others) + 1,
        "cohort_mean": mean,
        "cohort_sd": sd,
        "safer_than_cohort": asr_loose < mean,
    }


def judge_calibration() -> dict:
    return cohort()["judge_calibration"]


def cohort_models() -> dict:
    return cohort()["models"]


def nearest_models(asr_loose: float, k: int = 3) -> list[tuple[str, float]]:
    """The cohort models closest to this score, for context in the report."""
    ms = [(name, m["asr_loose"]) for name, m in cohort()["models"].items()]
    ms.sort(key=lambda x: abs(x[1] - asr_loose))
    return ms[:k]
