from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ReflectionEntry:
    timestamp: datetime
    session_id: str
    decision: str
    outcome: str
    confidence: Optional[float]
    impact: Optional[float]
    raw: Dict[str, Any]


class AutoReflectionAnalyzer:
    def __init__(self, log_dir: Path | str = "logs") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def iter_reflection_entries(self, *, since_days: Optional[int] = None) -> Iterable[ReflectionEntry]:
        cutoff = _now_utc() - timedelta(days=since_days) if since_days is not None else None
        for path in sorted(self.log_dir.glob("auto_reflection_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                entry = self._normalize_entry(item)
                if entry is None:
                    continue
                if cutoff is not None and entry.timestamp < cutoff:
                    continue
                yield entry

    def _normalize_entry(self, item: Dict[str, Any]) -> Optional[ReflectionEntry]:
        timestamp = _parse_timestamp(item.get("timestamp"))
        if timestamp is None:
            return None

        details = item.get("details")
        details_map = details if isinstance(details, dict) else {}
        session_id = str(
            item.get("session_id")
            or details_map.get("session_id")
            or details_map.get("session")
            or "unknown-session"
        )
        decision = str(
            item.get("decision")
            or item.get("action")
            or details_map.get("decision")
            or details_map.get("action")
            or "unknown-decision"
        )
        outcome = str(
            item.get("outcome")
            or details_map.get("result")
            or details_map.get("outcome")
            or "unknown"
        )
        confidence = _safe_float(item.get("confidence"))
        if confidence is None:
            confidence = _safe_float(details_map.get("confidence"))
        impact = _safe_float(item.get("impact"))
        if impact is None:
            impact = _safe_float(details_map.get("impact"))

        return ReflectionEntry(
            timestamp=timestamp,
            session_id=session_id,
            decision=decision,
            outcome=outcome,
            confidence=confidence,
            impact=impact,
            raw=item,
        )

    def analyze(self, *, since_days: int = 30) -> Dict[str, Any]:
        entries = list(self.iter_reflection_entries(since_days=since_days))
        generated_at = _now_utc().isoformat()
        if not entries:
            return {
                "generated_at": generated_at,
                "since_days": since_days,
                "entry_count": 0,
                "session_count": 0,
                "decision_patterns": [],
                "cross_session_learning": {"strongest_patterns": [], "volatile_patterns": []},
                "trends": {"volume": "flat", "success_rate": "flat", "impact": "flat", "daily": []},
            }

        decision_buckets: Dict[str, List[ReflectionEntry]] = defaultdict(list)
        session_buckets: Dict[str, List[ReflectionEntry]] = defaultdict(list)
        daily_buckets: Dict[str, List[ReflectionEntry]] = defaultdict(list)
        for entry in entries:
            decision_buckets[entry.decision].append(entry)
            session_buckets[entry.session_id].append(entry)
            day_key = entry.timestamp.date().isoformat()
            daily_buckets[day_key].append(entry)

        patterns = self._decision_patterns(decision_buckets)
        trends = self._detect_trends(daily_buckets)
        learning = self._build_cross_session_learning(patterns, session_buckets)
        learning = self._merge_with_historical_learning(learning)

        return {
            "generated_at": generated_at,
            "since_days": since_days,
            "entry_count": len(entries),
            "session_count": len(session_buckets),
            "decision_patterns": patterns,
            "cross_session_learning": learning,
            "trends": trends,
        }

    def _decision_patterns(self, buckets: Dict[str, List[ReflectionEntry]]) -> List[Dict[str, Any]]:
        patterns: List[Dict[str, Any]] = []
        for decision, items in buckets.items():
            success = sum(1 for item in items if item.outcome.lower() in {"success", "restarted", "cleared", "ok"})
            confidence_values = [item.confidence for item in items if item.confidence is not None]
            impact_values = [item.impact for item in items if item.impact is not None]
            patterns.append(
                {
                    "decision": decision,
                    "count": len(items),
                    "success_rate": round((success / len(items)) * 100, 1),
                    "avg_confidence": round(sum(confidence_values) / len(confidence_values), 2)
                    if confidence_values
                    else None,
                    "avg_impact": round(sum(impact_values) / len(impact_values), 2) if impact_values else None,
                }
            )
        return sorted(patterns, key=lambda item: (-item["count"], -item["success_rate"], item["decision"]))

    def _detect_trends(self, daily_buckets: Dict[str, List[ReflectionEntry]]) -> Dict[str, Any]:
        points: List[Dict[str, Any]] = []
        for day in sorted(daily_buckets):
            entries = daily_buckets[day]
            success = sum(1 for item in entries if item.outcome.lower() in {"success", "restarted", "cleared", "ok"})
            impact_values = [item.impact for item in entries if item.impact is not None]
            points.append(
                {
                    "day": day,
                    "volume": len(entries),
                    "success_rate": round((success / len(entries)) * 100, 1) if entries else 0.0,
                    "avg_impact": round(sum(impact_values) / len(impact_values), 2) if impact_values else 0.0,
                }
            )

        def label(metric: str) -> str:
            if len(points) < 2:
                return "flat"
            half = max(1, len(points) // 2)
            first = points[:half]
            second = points[half:]
            first_avg = sum(point[metric] for point in first) / len(first)
            second_avg = sum(point[metric] for point in second) / len(second)
            delta = second_avg - first_avg
            if delta > 0.25:
                return "up"
            if delta < -0.25:
                return "down"
            return "flat"

        return {
            "volume": label("volume"),
            "success_rate": label("success_rate"),
            "impact": label("avg_impact"),
            "daily": points,
        }

    def _build_cross_session_learning(
        self,
        patterns: List[Dict[str, Any]],
        session_buckets: Dict[str, List[ReflectionEntry]],
    ) -> Dict[str, Any]:
        per_decision_sessions: Dict[str, set[str]] = defaultdict(set)
        for session_id, entries in session_buckets.items():
            for entry in entries:
                per_decision_sessions[entry.decision].add(session_id)

        strongest_patterns: List[Dict[str, Any]] = []
        volatile_patterns: List[Dict[str, Any]] = []
        for pattern in patterns:
            decision = pattern["decision"]
            session_span = len(per_decision_sessions[decision])
            enriched = dict(pattern)
            enriched["session_span"] = session_span
            if pattern["success_rate"] >= 70 and session_span >= 2:
                strongest_patterns.append(enriched)
            elif pattern["success_rate"] <= 40 and session_span >= 2:
                volatile_patterns.append(enriched)

        strongest_patterns.sort(key=lambda item: (-item["success_rate"], -item["session_span"], item["decision"]))
        volatile_patterns.sort(key=lambda item: (item["success_rate"], -item["session_span"], item["decision"]))
        return {
            "strongest_patterns": strongest_patterns[:5],
            "volatile_patterns": volatile_patterns[:5],
        }

    def _merge_with_historical_learning(self, learning: Dict[str, Any]) -> Dict[str, Any]:
        path = self.log_dir / "auto_reflection_learning.json"
        existing: Dict[str, Any] = {"seen_decisions": {}, "runs": 0}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass

        seen = existing.get("seen_decisions")
        if not isinstance(seen, dict):
            seen = {}

        for group_name in ("strongest_patterns", "volatile_patterns"):
            for item in learning.get(group_name, []):
                decision = str(item.get("decision", "unknown-decision"))
                score = float(item.get("success_rate", 0.0))
                prev = seen.get(decision)
                if isinstance(prev, dict):
                    prev_avg = _safe_float(prev.get("avg_success_rate")) or 0.0
                    prev_count = int(prev.get("observations") or 0)
                else:
                    prev_avg = 0.0
                    prev_count = 0
                new_count = prev_count + 1
                new_avg = ((prev_avg * prev_count) + score) / new_count
                seen[decision] = {
                    "avg_success_rate": round(new_avg, 2),
                    "observations": new_count,
                }

        existing["seen_decisions"] = seen
        existing["runs"] = int(existing.get("runs") or 0) + 1
        existing["last_updated"] = _now_utc().isoformat()
        path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")

        learned = sorted(
            (
                {"decision": name, **stats}
                for name, stats in seen.items()
                if isinstance(stats, dict) and int(stats.get("observations") or 0) >= 2
            ),
            key=lambda item: (-float(item.get("avg_success_rate") or 0.0), -int(item.get("observations") or 0)),
        )
        learning["historical_best"] = learned[:5]
        learning["run_count"] = existing["runs"]
        return learning

    def render_digest(self, analysis: Dict[str, Any]) -> str:
        lines = [
            "# Auto Reflection Digest",
            "",
            f"- Generated: {analysis.get('generated_at', 'unknown')}",
            f"- Window: last {analysis.get('since_days', '?')} days",
            f"- Entries analyzed: {analysis.get('entry_count', 0)}",
            f"- Sessions covered: {analysis.get('session_count', 0)}",
            "",
            "## Decision Patterns",
            "",
            "| Decision | Count | Success % | Avg confidence | Avg impact |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for pattern in analysis.get("decision_patterns", []):
            lines.append(
                "| {decision} | {count} | {success_rate} | {avg_confidence} | {avg_impact} |".format(
                    decision=pattern.get("decision", "unknown"),
                    count=pattern.get("count", 0),
                    success_rate=pattern.get("success_rate", 0),
                    avg_confidence=pattern.get("avg_confidence", "-"),
                    avg_impact=pattern.get("avg_impact", "-"),
                )
            )
        if not analysis.get("decision_patterns"):
            lines.append("| _No data_ | 0 | 0 | - | - |")

        learning = analysis.get("cross_session_learning", {})
        lines.extend(
            [
                "",
                "## Cross-Session Learning",
                "",
                "### Strongest repeatable decisions",
            ]
        )
        strongest = learning.get("strongest_patterns", [])
        if strongest:
            lines.extend(
                f"- `{item['decision']}`: {item['success_rate']}% success across {item['session_span']} sessions"
                for item in strongest
            )
        else:
            lines.append("- No repeatable strong patterns yet.")

        lines.append("")
        lines.append("### Volatile decisions to revisit")
        volatile = learning.get("volatile_patterns", [])
        if volatile:
            lines.extend(
                f"- `{item['decision']}`: {item['success_rate']}% success across {item['session_span']} sessions"
                for item in volatile
            )
        else:
            lines.append("- No volatile multi-session decisions detected.")

        lines.extend(["", "## Trend Detection", ""])
        trends = analysis.get("trends", {})
        lines.append(
            "- Direction: volume={volume}, success={success_rate}, impact={impact}".format(
                volume=trends.get("volume", "flat"),
                success_rate=trends.get("success_rate", "flat"),
                impact=trends.get("impact", "flat"),
            )
        )
        lines.append("")
        lines.append("| Day | Volume | Success % | Avg impact |")
        lines.append("| --- | ---: | ---: | ---: |")
        daily = trends.get("daily", [])
        if daily:
            for row in daily:
                lines.append(
                    "| {day} | {volume} | {success_rate} | {avg_impact} |".format(
                        day=row.get("day", "unknown"),
                        volume=row.get("volume", 0),
                        success_rate=row.get("success_rate", 0),
                        avg_impact=row.get("avg_impact", 0),
                    )
                )
        else:
            lines.append("| _No data_ | 0 | 0 | 0 |")

        return "\n".join(lines) + "\n"

    def run(self, *, since_days: int = 30) -> Tuple[Dict[str, Any], str]:
        analysis = self.analyze(since_days=since_days)
        digest = self.render_digest(analysis)
        digest_path = self.log_dir / "auto_reflection_digest.md"
        analysis_path = self.log_dir / "auto_reflection_analysis.json"
        digest_path.write_text(digest, encoding="utf-8")
        analysis_path.write_text(json.dumps(analysis, indent=2, sort_keys=True), encoding="utf-8")
        return analysis, digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze reflection logs and generate digest output.")
    parser.add_argument("--log-dir", default="logs", help="Directory containing auto_reflection_*.json logs.")
    parser.add_argument("--days", type=int, default=30, help="How many trailing days to analyze.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    analyzer = AutoReflectionAnalyzer(log_dir=Path(args.log_dir))
    _, digest = analyzer.run(since_days=args.days)
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
