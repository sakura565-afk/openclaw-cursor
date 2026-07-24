#!/usr/bin/env python3
"""ComfyUI multi-machine pool: registry, health checks, and least-busy selection.

Discovers Local + Work-PC endpoints (same defaults as ``comfy_auto_quality``),
probes ``/system_stats`` and ``/queue``, and picks the least-loaded online host.

Environment overrides:
    COMFY_MACHINES   JSON list of ``{"name","url"}`` or comma-separated URLs
    COMFY_URL        single URL treated as machine ``custom``

Usage::

    python -m scripts.comfy_machine_pool status
    python -m scripts.comfy_machine_pool pick
    python -m scripts.comfy_machine_pool ping --name Local
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


DEFAULT_LOCAL_URL = "http://127.0.0.1:8188"
DEFAULT_WORK_PC_URL = "http://192.168.31.180:8188"
DEFAULT_TIMEOUT_SEC = 5.0


@dataclass(frozen=True)
class Machine:
    """One ComfyUI HTTP endpoint."""

    name: str
    url: str
    priority: int = 100  # lower = preferred when load is equal

    def normalized_url(self) -> str:
        return self.url.rstrip("/")


@dataclass
class MachineStatus:
    """Snapshot of reachability and queue load."""

    machine: Machine
    online: bool
    queue_pending: int = 0
    queue_running: int = 0
    vram_free_mb: Optional[float] = None
    vram_total_mb: Optional[float] = None
    error: Optional[str] = None
    probed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    @property
    def load_score(self) -> float:
        """Lower is better. Offline machines sort last."""
        if not self.online:
            return 1e12
        # Prefer fewer queued jobs; break ties with priority then free VRAM.
        vram_penalty = 0.0
        if self.vram_total_mb and self.vram_free_mb is not None:
            used_ratio = 1.0 - (self.vram_free_mb / max(self.vram_total_mb, 1.0))
            vram_penalty = used_ratio * 0.5
        return (
            float(self.queue_pending + self.queue_running)
            + vram_penalty
            + self.machine.priority * 0.001
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["machine"] = asdict(self.machine)
        data["load_score"] = self.load_score
        return data


def _http_json(url: str, timeout: float) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def default_machines() -> List[Machine]:
    """Built-in Local + Work-PC pool (matches ``comfy_auto_quality``)."""
    return [
        Machine(name="Local", url=DEFAULT_LOCAL_URL, priority=10),
        Machine(name="Work-PC", url=DEFAULT_WORK_PC_URL, priority=20),
    ]


def machines_from_env(env: Optional[Dict[str, str]] = None) -> Optional[List[Machine]]:
    """Parse ``COMFY_MACHINES`` / ``COMFY_URL`` if set; else ``None``."""
    environ = env if env is not None else os.environ
    raw = environ.get("COMFY_MACHINES", "").strip()
    if raw:
        if raw.startswith("["):
            parsed = json.loads(raw)
            machines: List[Machine] = []
            for i, item in enumerate(parsed):
                if isinstance(item, str):
                    machines.append(Machine(name=f"machine-{i + 1}", url=item, priority=10 + i))
                elif isinstance(item, dict):
                    machines.append(
                        Machine(
                            name=str(item.get("name") or f"machine-{i + 1}"),
                            url=str(item["url"]),
                            priority=int(item.get("priority", 10 + i)),
                        )
                    )
                else:
                    raise ValueError(f"Invalid COMFY_MACHINES entry: {item!r}")
            return machines
        # Comma-separated URLs
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        return [Machine(name=f"machine-{i + 1}", url=u, priority=10 + i) for i, u in enumerate(urls)]

    single = environ.get("COMFY_URL", "").strip()
    if single:
        return [Machine(name="custom", url=single, priority=10)]
    return None


def resolve_machines(
    machines: Optional[Sequence[Machine]] = None,
    env: Optional[Dict[str, str]] = None,
) -> List[Machine]:
    if machines is not None:
        return list(machines)
    from_env = machines_from_env(env)
    if from_env:
        return from_env
    return default_machines()


def probe_machine(machine: Machine, timeout: float = DEFAULT_TIMEOUT_SEC) -> MachineStatus:
    """Hit ``/system_stats`` and ``/queue`` on one host."""
    base = machine.normalized_url()
    try:
        stats = _http_json(f"{base}/system_stats", timeout=timeout)
        queue = _http_json(f"{base}/queue", timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        return MachineStatus(machine=machine, online=False, error=str(exc))

    pending = queue.get("queue_pending") or []
    running = queue.get("queue_running") or []
    vram_free: Optional[float] = None
    vram_total: Optional[float] = None
    devices = stats.get("devices") if isinstance(stats, dict) else None
    if isinstance(devices, list) and devices:
        dev0 = devices[0] if isinstance(devices[0], dict) else {}
        # ComfyUI reports bytes
        free_b = dev0.get("vram_free")
        total_b = dev0.get("vram_total")
        if isinstance(free_b, (int, float)):
            vram_free = float(free_b) / (1024 * 1024)
        if isinstance(total_b, (int, float)):
            vram_total = float(total_b) / (1024 * 1024)

    return MachineStatus(
        machine=machine,
        online=True,
        queue_pending=len(pending) if isinstance(pending, list) else int(pending or 0),
        queue_running=len(running) if isinstance(running, list) else int(running or 0),
        vram_free_mb=vram_free,
        vram_total_mb=vram_total,
    )


class MachinePool:
    """Registry of ComfyUI hosts with health probing and least-busy pick."""

    def __init__(
        self,
        machines: Optional[Sequence[Machine]] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        self.machines = resolve_machines(machines, env=env)
        self.timeout = timeout

    def probe_all(self) -> List[MachineStatus]:
        return [probe_machine(m, timeout=self.timeout) for m in self.machines]

    def online(self) -> List[MachineStatus]:
        return [s for s in self.probe_all() if s.online]

    def pick(self, *, require_online: bool = True) -> Optional[MachineStatus]:
        """Return the least-loaded machine, or ``None`` if none are online."""
        statuses = self.probe_all()
        candidates = [s for s in statuses if s.online] if require_online else statuses
        if not candidates:
            return None
        return min(candidates, key=lambda s: s.load_score)

    def get(self, name: str) -> Optional[Machine]:
        key = name.strip().lower()
        for machine in self.machines:
            if machine.name.lower() == key:
                return machine
        return None

    def status_table(self) -> str:
        rows = self.probe_all()
        lines = [
            f"{'NAME':<12} {'ONLINE':<7} {'PEND':>4} {'RUN':>4} {'VRAM_FREE':>10} {'URL'}",
            "-" * 72,
        ]
        for s in sorted(rows, key=lambda x: x.load_score):
            vram = f"{s.vram_free_mb:.0f}M" if s.vram_free_mb is not None else "-"
            lines.append(
                f"{s.machine.name:<12} {str(s.online):<7} {s.queue_pending:>4} "
                f"{s.queue_running:>4} {vram:>10} {s.machine.normalized_url()}"
            )
            if s.error:
                lines.append(f"  error: {s.error}")
        return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ComfyUI multi-machine pool: status, pick, ping.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SEC,
        help="HTTP timeout seconds per probe.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Probe all machines and print a table.")
    sub.add_parser("pick", help="Print the least-busy online machine as JSON.")

    ping = sub.add_parser("ping", help="Probe one machine by name.")
    ping.add_argument("--name", required=True, help="Machine name (e.g. Local, Work-PC).")

    list_p = sub.add_parser("list", help="List configured machines without probing.")
    list_p.add_argument("--json", action="store_true", help="Emit JSON.")

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    pool = MachinePool(timeout=args.timeout)

    if args.command == "list":
        payload = [asdict(m) for m in pool.machines]
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
        else:
            for m in pool.machines:
                print(f"{m.name}\t{m.normalized_url()}\tpriority={m.priority}")
        return 0

    if args.command == "status":
        print(pool.status_table())
        online = pool.online()
        return 0 if online else 1

    if args.command == "pick":
        chosen = pool.pick()
        if chosen is None:
            print(json.dumps({"error": "no online ComfyUI machines"}), file=sys.stderr)
            return 1
        print(json.dumps(chosen.to_dict(), indent=2))
        return 0

    if args.command == "ping":
        machine = pool.get(args.name)
        if machine is None:
            print(f"Unknown machine: {args.name}", file=sys.stderr)
            print(f"Known: {', '.join(m.name for m in pool.machines)}", file=sys.stderr)
            return 1
        status = probe_machine(machine, timeout=args.timeout)
        print(json.dumps(status.to_dict(), indent=2))
        return 0 if status.online else 1

    parser_err = f"Unknown command: {args.command}"
    print(parser_err, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
