"""Run the existing H3 chain with survival/structure instead of energy gating."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.h3_chain_pipeline_kimi import pipeline as h3

from .gate import GateConfig, evaluate_metrics, measure_added_texture


class TextureChainPipeline(h3.ChainPipeline):
    """Reuse rendering/resume/assembly and replace only chunk measurement/gating."""

    def __init__(self, config: h3.ChainConfig, texture_config: GateConfig | None = None):
        super().__init__(config)
        self.texture_config = replace(
            texture_config or GateConfig(), max_face_drift=config.max_face_drift
        )

    def _fingerprint(self) -> str:
        """Threshold changes must not reuse chunks accepted under another gate."""
        base = super()._fingerprint()
        encoded = json.dumps(asdict(self.texture_config), sort_keys=True)
        return hashlib.sha256(f"{base}:{encoded}".encode("utf-8")).hexdigest()

    def _baseline(self, chunk_index: int) -> tuple[int | None, Mapping[str, object] | None]:
        """The first accepted chunk stays the baseline for the whole chain."""
        for previous in self.state["chunks"][:chunk_index]:
            if previous.get("status") == "selected" and previous.get("gate_passed"):
                metrics = previous.get("metrics")
                if isinstance(metrics, dict):
                    return int(previous["index"]), metrics
        return None, None

    def _measure_chunk(self, video: Path, attempt_dir: Path, anchor: Path) -> dict[str, Any]:
        """Keep the existing mean/tail drift extraction, then add delta survival."""
        metrics = dict(super()._measure_chunk(video, attempt_dir))
        samples = sorted((attempt_dir / "samples").glob("frame_*.png"))
        if not samples:
            raise RuntimeError("existing chain measurement produced no sample frames")
        metrics.update(measure_added_texture(anchor, samples))
        return metrics

    @staticmethod
    def _sort_value(metrics: Mapping[str, Any], key: str) -> float:
        value = metrics.get(key)
        return float(value) if h3._finite(value) else float("inf")

    def _best_attempt(
        self,
        attempts: Sequence[dict[str, Any]],
        baseline: Mapping[str, object] | None,
    ) -> dict[str, Any]:
        candidates = [attempt for attempt in attempts if isinstance(attempt.get("metrics"), dict)]
        if not candidates:
            raise RuntimeError("no measured chunk attempt is available")

        def key(attempt: dict[str, Any]) -> tuple[float, float, float, float, float, int]:
            metrics = attempt["metrics"]
            result = evaluate_metrics(metrics, baseline=baseline, config=self.texture_config)
            return (
                result.penalty,
                self._sort_value(metrics, "face_drift_tail"),
                self._sort_value(metrics, "face_drift"),
                -self._sort_value(metrics, "structure_score"),
                -self._sort_value(metrics, "survival"),
                int(attempt["attempt"]),
            )

        return min(candidates, key=key)

    def _record_selection(
        self, chunk: dict[str, Any], selected: dict[str, Any], baseline_chunk: int | None
    ) -> None:
        self._select_attempt(chunk, selected)
        chunk["texture_baseline_chunk"] = baseline_chunk
        chunk["texture_survival_floor"] = selected["survival_floor"]
        chunk["texture_structure_floor"] = selected["structure_floor"]

    def _process_chunk(self, index: int, image_name: str) -> None:
        """Mirror the existing retry loop, substituting the new gate only."""
        chunk = self.state["chunks"][index]
        if chunk.get("status") == "selected":
            return
        plan = self.plan[index]
        baseline_chunk, baseline = self._baseline(index)
        chunk["status"] = "rendering"
        h3.save_state(self.config.state, self.state)

        while len(chunk["attempts"]) <= self.config.max_retries:
            attempt_number = len(chunk["attempts"])
            seed = h3.seed_for(self.config.seed_base, index, attempt_number)
            attempt_dir = self.config.work_dir / f"chunk_{index:03d}" / f"attempt_{attempt_number:03d}"
            output = attempt_dir / f"chunk_{index}_{attempt_number}.mp4"
            record: dict[str, Any] = {
                "attempt": attempt_number,
                "seed": seed,
                "image_name": image_name,
                "video": str(output),
                "baseline_chunk": baseline_chunk,
                "accepted": False,
            }
            try:
                self._render_attempt(
                    chunk_index=index,
                    attempt=attempt_number,
                    seed=seed,
                    image_name=image_name,
                    output=output,
                    prompt_file=self.config.prompt_file,
                    audio=self.config.audio,
                    prefix=f"h3chain_{self.job_id}_c{index:03d}_a{attempt_number:03d}",
                    width=self.config.width,
                    height=self.config.height,
                    render_frames=plan.render_frames,
                    steps=self.config.steps,
                    identity_lora=self.config.identity_lora,
                    identity_image_name=self._identity_image_name,
                )
                anchor = self._comfy_input_dir() / image_name
                metrics = self._measure_chunk(output, attempt_dir, anchor)
                gate = evaluate_metrics(metrics, baseline=baseline, config=self.texture_config)
                record.update(
                    status="accepted" if gate.accepted else "rejected",
                    accepted=gate.accepted,
                    metrics=metrics,
                    reasons=gate.reasons,
                    penalty=gate.penalty,
                    survival_floor=gate.survival_floor,
                    structure_floor=gate.structure_floor,
                )
            except Exception as exc:
                record.update(status="render_error", error=str(exc), reasons=[str(exc)])
            chunk["attempts"].append(record)
            h3.save_state(self.config.state, self.state)
            if record["accepted"]:
                self._record_selection(chunk, record, baseline_chunk)
                break
        else:
            existing = [
                attempt
                for attempt in chunk["attempts"]
                if attempt.get("video") and Path(attempt["video"]).is_file()
            ]
            selected = self._best_attempt(existing, baseline)
            # A best-effort fallback remains visibly rejected in state/report.
            selected.setdefault("survival_floor", self.texture_config.min_survival)
            selected.setdefault("structure_floor", self.texture_config.min_structure)
            self._record_selection(chunk, selected, baseline_chunk)

        selected_video = Path(chunk["video"])
        last_frame = self.config.work_dir / "anchors" / f"chunk_{index:03d}_last.png"
        self._extract_last_frame(selected_video, last_frame)
        chunk["last_frame"] = str(last_frame)
        h3.save_state(self.config.state, self.state)

    def _report(self, seams: list[dict[str, Any]]) -> dict[str, Any]:
        report = super()._report(seams)
        selected = self.state["chunks"]
        survivals = [float(chunk["metrics"]["survival"]) for chunk in selected]
        structures = [float(chunk["metrics"]["structure_score"]) for chunk in selected]
        raw_survivals = [float(chunk["metrics"]["texture_survival"]) for chunk in selected]
        report["texture_gate"] = asdict(self.texture_config)
        report["summary"].update(
            texture_added_survival_mean=statistics.mean(survivals),
            texture_added_survival_min=min(survivals),
            texture_raw_survival_mean=statistics.mean(raw_survivals),
            texture_structure_mean=statistics.mean(structures),
            texture_structure_min=min(structures),
            texture_baseline_chunks=[
                chunk.get("texture_baseline_chunk") for chunk in selected
            ],
        )
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = h3.build_parser()
    parser.description = __doc__
    # Keep ChainConfig construction compatible, but do not silently reintroduce
    # the old energy ratchet. The two legacy options are hidden and rejected.
    for action in parser._actions:
        if action.dest in {"min_texture", "max_texture_drop"}:
            action.help = argparse.SUPPRESS
    parser.set_defaults(min_texture=0.0, max_texture_drop=0.0)
    parser.add_argument("--min-survival", type=float, default=GateConfig.min_survival)
    parser.add_argument("--min-structure", type=float, default=GateConfig.min_structure)
    parser.add_argument(
        "--max-survival-drop", type=float, default=GateConfig.max_survival_drop
    )
    parser.add_argument(
        "--max-structure-drop", type=float, default=GateConfig.max_structure_drop
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.min_texture != 0.0 or args.max_texture_drop != 0.0:
            raise ValueError(
                "legacy pore-energy gate options are disabled; use survival/structure thresholds"
            )
        config = h3._config_from_args(args)
        texture_config = GateConfig(
            min_survival=args.min_survival,
            min_structure=args.min_structure,
            max_survival_drop=args.max_survival_drop,
            max_structure_drop=args.max_structure_drop,
            max_face_drift=config.max_face_drift,
        )
        plan = h3.build_chunk_plan(config.total_frames, config.chunk_frames, config.seed_base)
        if args.plan:
            print(h3.format_plan(plan, config.total_frames))
            print(
                "texture gate: "
                f"added survival>={texture_config.min_survival:.3f}, "
                f"structure>={texture_config.min_structure:.3f}; "
                f"baseline drops<={texture_config.max_survival_drop:.0%}/"
                f"{texture_config.max_structure_drop:.0%}"
            )
            return 0
        report = TextureChainPipeline(config, texture_config).run()
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"h3 survival-gated chain failed: {exc}", file=h3.sys.stderr)
        return 2
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {config.report_path}")
    return 0 if report["summary"]["quality_gate_passed"] else 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
