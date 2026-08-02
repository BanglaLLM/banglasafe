"""Configuration: CLI flag > environment variable > YAML file > packaged default."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(__file__).parent / "data"
RUBRIC_PATH = DATA_DIR / "rubric.md"
REFERENCE_PATH = DATA_DIR / "reference_cohort.json"

HF_DATASET = "BanglaLLM/BanglaSafe"

LABELS = ("REFUSE", "POLICY", "PARTIAL", "HARMFUL")
UNSAFE_LOOSE = ("PARTIAL", "HARMFUL")
UNSAFE_STRICT = ("HARMFUL",)

CONDITIONS = ("EN_Direct", "EN_Inst", "BN_Formal", "BN_Collq", "BN_Inst")

# Decoding held fixed so a score reflects the model's policy, not sampling noise.
# Huang et al. (2024) show decoding variation alone moves open-model safety rates
# from near zero to >95% on identical prompts.
TEMPERATURE = 0.0
TOP_P = 1.0

BOOTSTRAP_B = 10_000
BOOTSTRAP_SEED = 20260521

# Judges with published calibration against human gold on this benchmark.
CALIBRATED_JUDGES = {
    "anthropic/claude-opus-4-7": {
        "kappa_vs_human_binary": 0.666,
        "note": "The judge used for the published labels. Calibrated against human gold on 300 responses.",
    },
    "google/gemini-3.1-pro": {
        "kappa_vs_human_binary": None,
        "cross_judge_binary_kappa": 0.787,
        "cross_judge_pearson_r": 0.989,
        "note": "Independent second judge. Agrees with the calibrated judge at r=0.989 across 18 models.",
    },
}


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def normalize_base_url(url: str) -> str:
    """Accept either a /v1 base or a full chat-completions URL.

    Users paste both. The OpenAI SDK wants the base, so strip the endpoint suffix
    if present rather than failing with a confusing 404.
    """
    url = url.strip().rstrip("/")
    url = re.sub(r"/chat/completions$", "", url)
    url = re.sub(r"/completions$", "", url)
    return url


@dataclass
class Endpoint:
    model: str
    base_url: str
    api_key: str
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        self.base_url = normalize_base_url(self.base_url)


@dataclass
class Settings:
    target: Endpoint
    judge: Endpoint
    max_concurrency: int = 8
    output_dir: Path = field(default_factory=lambda: Path("results"))
    limit: int | None = None
    conditions: tuple[str, ...] | None = None
    categories: tuple[str, ...] | None = None


def load_yaml(path: Path | None) -> dict[str, Any]:
    """Load a config file, expanding {{ env.VAR }} so secrets stay out of the file."""
    if path is None:
        return {}
    raw = Path(path).read_text()
    raw = re.sub(r"\{\{\s*env\.([A-Z0-9_]+)\s*\}\}", lambda m: os.environ.get(m.group(1), ""), raw)
    return yaml.safe_load(raw) or {}


def resolve(
    *,
    model: str,
    base_url: str | None,
    api_key: str | None,
    judge_model: str,
    judge_base_url: str | None,
    judge_api_key: str | None,
    max_tokens: int,
    max_concurrency: int,
    output_dir: Path,
    limit: int | None,
    conditions: list[str] | None,
    categories: list[str] | None,
    config_path: Path | None,
) -> Settings:
    """Merge CLI flags over env vars over YAML. CLI always wins."""
    cfg = load_yaml(config_path)
    t_cfg = cfg.get("target", {}) or {}
    j_cfg = cfg.get("judge", {}) or {}
    r_cfg = cfg.get("run", {}) or {}

    base = (
        base_url
        or _env("BANGLASAFE_BASE_URL", "OPENAI_BASE_URL", "VLLM_BASE_URL", "SGLANG_BASE_URL", "OLLAMA_BASE_URL")
        or t_cfg.get("base_url")
        or "https://openrouter.ai/api/v1"
    )
    key = api_key or _env("BANGLASAFE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY") or t_cfg.get("api_key") or "local"

    j_base = judge_base_url or _env("BANGLASAFE_JUDGE_BASE_URL") or j_cfg.get("base_url") or base
    j_key = judge_api_key or _env("BANGLASAFE_JUDGE_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY") or j_cfg.get("api_key") or key

    return Settings(
        target=Endpoint(model=model, base_url=base, api_key=key, max_tokens=max_tokens),
        judge=Endpoint(model=judge_model, base_url=j_base, api_key=j_key, max_tokens=1024),
        max_concurrency=max_concurrency or r_cfg.get("max_concurrency", 8),
        output_dir=Path(output_dir),
        limit=limit,
        conditions=tuple(conditions) if conditions else (tuple(r_cfg["conditions"]) if r_cfg.get("conditions") else None),
        categories=tuple(categories) if categories else (tuple(r_cfg["categories"]) if r_cfg.get("categories") else None),
    )
