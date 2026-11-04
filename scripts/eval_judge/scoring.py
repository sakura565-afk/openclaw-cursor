"""Aggregation of the external judge's scores and the final verdict.

The judge is external by construction: this package cannot call a vision
model, so it prepares the artefacts and prints a ready-made prompt, and the
scores come back as JSON through ``--scores``.

Accepted shapes (all optional wrappers are tolerated):

    {"A": {"pore_readability": 7, "naturalness": 6, ...}}
    {"scores": {"A": {"pore_readability": [7, 6, 8], ...}}}
    {"scores": [{"variant": "A", "pore_readability": 7, ...}]}
    [{"variant": "A", "pore_readability": 7, ...}]

A criterion value may be a number or a list of numbers (several judgements of
the same variant - the point of the median/IQR is to make the disagreement
visible instead of hiding it behind one lucky number). Keys are matched
against the blind letters AND against the candidate names, so a judge that
saw the file names still works.

The verdict combines the judge scores with the objective measurements, and it
refuses to read a flicker number for a clip that is not comparable with the
reference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .metrics import CRITERIA, canonical_criterion, iqr, median

# A judge whose IQR on a criterion is wider than this has not really decided.
UNSTABLE_IQR = 3.0
# Below this the judge has not separated the variants at all.
DECISIVE_MARGIN = 0.5


def load_scores(path: str | Path) -> dict[str, dict[str, list[float]]]:
    """Read a judge score file into ``{variant: {criterion: [values]}}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalise_scores(data)


def normalise_scores(data: Any) -> dict[str, dict[str, list[float]]]:
    """Turn any accepted score shape into the canonical form."""
    if isinstance(data, Mapping):
        for wrapper in ("scores", "judge_scores", "results"):
            if wrapper in data and isinstance(data[wrapper], (Mapping, list)):
                data = data[wrapper]
                break
    out: dict[str, dict[str, list[float]]] = {}

    def record(variant: Any, payload: Any) -> None:
        if not isinstance(payload, Mapping):
            return
        key = str(variant)
        bucket = out.setdefault(key, {})
        for raw_name, raw_value in payload.items():
            criterion = canonical_criterion(str(raw_name))
            if criterion is None:
                continue
            values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
            numbers = [float(v) for v in values if isinstance(v, (int, float))]
            if numbers:
                bucket.setdefault(criterion, []).extend(numbers)

    if isinstance(data, Mapping):
        for variant, payload in data.items():
            record(variant, payload)
    elif isinstance(data, list):
        for entry in data:
            if not isinstance(entry, Mapping):
                continue
            variant = entry.get("variant") or entry.get("label") or entry.get("id") or ""
            payload = {
                key: value
                for key, value in entry.items()
                if key not in ("variant", "label", "id")
            }
            record(variant, payload)
    return out


def aggregate(
    scores: Mapping[str, Mapping[str, Sequence[float]]],
    labels: Mapping[str, str],
) -> dict:
    """Per-variant statistics and the judge's verdict.

    ``labels`` maps the judge's key (blind letter or candidate name) onto the
    blind letter, so the report speaks one language.
    """
    per_variant: dict[str, dict] = {}
    for key, criteria in scores.items():
        blind = labels.get(key, key)
        entry: dict[str, Any] = {"judge_key": key, "blind_label": blind, "criteria": {}}
        composite: list[float] = []
        for criterion, spec in CRITERIA.items():
            raw = criteria.get(criterion, [])
            if raw is None:
                continue
            # A criterion may be a single number or a list of them.
            values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
            values = [float(value) for value in values if isinstance(value, (int, float))]
            if not values:
                continue
            stats = {
                "values": values,
                "n": len(values),
                "median": round(median(values), 4),
                "q1": round(_q(values, 0.25), 4),
                "q3": round(_q(values, 0.75), 4),
                "iqr": round(iqr(values), 4),
                "higher_is_better": bool(spec["higher_is_better"]),
            }
            stats["unstable"] = stats["iqr"] > UNSTABLE_IQR
            entry["criteria"][criterion] = stats
            score = stats["median"] if spec["higher_is_better"] else -stats["median"]
            composite.append(score)
        if composite:
            entry["composite"] = round(sum(composite) / len(composite), 4)
        per_variant[blind] = entry

    ranking = sorted(
        (entry for entry in per_variant.values() if "composite" in entry),
        key=lambda entry: entry["composite"],
        reverse=True,
    )
    for position, entry in enumerate(ranking, start=1):
        entry["rank"] = position

    verdict: dict[str, Any] = {"ranking": [entry["blind_label"] for entry in ranking]}
    if not ranking:
        verdict["status"] = "no scores"
        return {"variants": per_variant, "verdict": verdict}

    best = ranking[0]
    if len(ranking) > 1:
        margin = best["composite"] - ranking[1]["composite"]
    else:
        margin = float("inf")
    verdict["winner"] = best["blind_label"]
    verdict["margin"] = None if margin == float("inf") else round(margin, 4)
    verdict["decisive"] = margin > DECISIVE_MARGIN

    unstable = sorted(
        {
            f"{entry['blind_label']}:{criterion}"
            for entry in per_variant.values()
            for criterion, stats in entry.get("criteria", {}).items()
            if stats.get("unstable")
        }
    )
    verdict["unstable_criteria"] = unstable
    if unstable:
        verdict["status"] = "unstable judgement"
    elif verdict["decisive"]:
        verdict["status"] = "decisive"
    else:
        verdict["status"] = "no clear winner"
    return {"variants": per_variant, "verdict": verdict}


def _q(values: Iterable[float], fraction: float) -> float:
    from .metrics import quartile

    return quartile(values, fraction)
