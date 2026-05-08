#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Goal Decomposer — break goals into actionable roadmaps.

Usage:
    python -m scripts.goal_decomposer decompose "GOAL TEXT" [--days 30|100]
    python -m scripts.goal_decomposer status TASK_ID
    python -m scripts.goal_decomposer list
"""

import argparse
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_DAYS = 30
TASKS_FILE = Path.home() / ".openclaw" / "workspace" / "goals.md"


@dataclass
class SubTask:
    title: str
    estimate_hours: float
    priority: str = "should"
    
@dataclass
class Task:
    id: str
    title: str
    estimate_hours: float
    priority: str
    subtasks: list[SubTask] = field(default_factory=list)
    status: str = "todo"
    
@dataclass
class Epic:
    title: str
    tasks: list[Task] = field(default_factory=list)
    priority: str = "should"


def parse_goal_text(text: str) -> list[str]:
    """Extract key objectives from goal text."""
    sentences = re.split(r"[.!?\n]+", text)
    objectives = []
    
    keywords = [
        "build", "create", "improve", "implement", "design", "develop",
        "set up", "automate", "integrate", "optimize", "launch", "deploy",
        "learn", "research", "analyze", "migrate", "refactor", "test"
    ]
    
    for s in sentences:
        s = s.strip()
        if len(s) < 10:
            continue
        s_lower = s.lower()
        if any(kw in s_lower for kw in keywords):
            objectives.append(s)
        elif len(s) > 20:
            objectives.append(s)
    
    return objectives[:10]


def estimate_task_hours(text: str) -> float:
    """Rough estimate based on keywords."""
    text_lower = text.lower()
    
    base = 4.0
    
    complex_keywords = [
        ("machine learning", 40), ("ai ", 30), ("database", 20),
        ("api", 15), ("integration", 25), ("pipeline", 30),
        ("refactor", 20), ("test", 15), ("deploy", 15),
        ("build", 20), ("design", 20), ("implement", 25),
        ("learning", 10), ("research", 10), ("analysis", 10)
    ]
    
    for kw, hours in complex_keywords:
        if kw in text_lower:
            base = max(base, hours)
    
    return base


def prioritize(text: str) -> str:
    """Determine priority based on keywords."""
    text_lower = text.lower()
    
    critical = ["urgent", "critical", "asap", "important", "priority"]
    must = ["must", "need", "essential", "core", "business"]
    nice = ["nice", "optional", "nice-to-have", "eventually", "future"]
    
    if any(w in text_lower for w in critical):
        return "must"
    if any(w in text_lower for w in must):
        return "should"
    return "nice"


def decompose_goal(goal_text: str, days: int = DEFAULT_DAYS) -> list[Epic]:
    """Decompose goal into epics, tasks, subtasks."""
    objectives = parse_goal_text(goal_text)
    
    epics = []
    
    # Core epic - most critical
    core_obj = objectives[:2] if objectives else ["Core implementation"]
    core_tasks = []
    
    for i, obj in enumerate(core_obj[:3], 1):
        tid = f"T{i:02d}"
        priority = prioritize(obj)
        hours = estimate_task_hours(obj)
        
        subtasks = []
        if hours > 20:
            subtasks.append(SubTask("Research and design", hours * 0.2, priority))
            subtasks.append(SubTask("Implementation", hours * 0.5, priority))
            subtasks.append(SubTask("Testing", hours * 0.2, priority))
            subtasks.append(SubTask("Documentation", hours * 0.1, priority))
        
        task = Task(id=tid, title=obj, estimate_hours=hours, priority=priority, subtasks=subtasks)
        core_tasks.append(task)
    
    core_epic = Epic(title="Core Objectives", tasks=core_tasks, priority="must")
    epics.append(core_epic)
    
    # Supporting epic
    if len(objectives) > 3:
        supp_tasks = []
        for i, obj in enumerate(objectives[3:6], len(core_tasks) + 1):
            tid = f"T{i:02d}"
            task = Task(id=tid, title=obj, estimate_hours=estimate_task_hours(obj), 
                       priority=prioritize(obj))
            supp_tasks.append(task)
        supp_epic = Epic(title="Supporting Tasks", tasks=supp_tasks, priority="should")
        epics.append(supp_epic)
    
    # Nice-to-have
    if len(objectives) > 6:
        nice_tasks = []
        for i, obj in enumerate(objectives[6:9], len(core_tasks) + len(supp_tasks) + 1):
            tid = f"T{i:02d}"
            task = Task(id=tid, title=obj, estimate_hours=estimate_task_hours(obj) * 0.5,
                       priority="nice")
            nice_tasks.append(task)
        nice_epic = Epic(title="Nice to Have", tasks=nice_tasks, priority="nice")
        epics.append(nice_epic)
    
    return epics


def format_roadmap(epics: list[Epic], goal: str, days: int) -> str:
    """Format roadmaps as Obsidian-compatible markdown."""
    lines = [
        f"# Goal Roadmap",
        f"",
        f"**Goal:** {goal}",
        f"**Timeframe:** {days} days",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        "---",
        ""
    ]
    
    for epic in epics:
        priority_emoji = {"must": "[MUST]", "should": "[SHOULD]", "nice": "[NICE]"}[epic.priority]
        lines.append(f"## {priority_emoji} {epic.title}")
        lines.append("")
        
        total_hours = sum(t.estimate_hours for t in epic.tasks)
        lines.append(f"*Total estimate: {total_hours:.0f} hours*")
        lines.append("")
        
        for task in epic.tasks:
            emoji = {"must": "🔴", "should": "🟡", "nice": "🟢"}[task.priority]
            status_icon = {"todo": "🔲", "in_progress": "🔄", "done": "✅"}[task.status]
            
            lines.append(f"{emoji} **{task.id}** {status_icon} {task.title}")
            lines.append(f"   - Priority: {task.priority.upper()}")
            lines.append(f"   - Estimate: {task.estimate_hours:.0f}h")
            
            if task.subtasks:
                lines.append("   - Subtasks:")
                for st in task.subtasks:
                    lines.append(f"     - [ ] {st.title} ({st.estimate_hours:.0f}h)")
            
            lines.append("")
        
        lines.append("---")
        lines.append("")
    
    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Priority | Tasks | Hours |")
    lines.append("|----------|-------|-------|")
    
    for epic in epics:
        tasks = len(epic.tasks)
        hours = sum(t.estimate_hours for t in epic.tasks)
        lines.append(f"| {epic.priority.upper()} | {tasks} | {hours:.0f}h |")
    
    total_hours = sum(sum(t.estimate_hours for t in e.tasks) for e in epics)
    lines.append(f"| **TOTAL** | **{sum(len(e.tasks) for e in epics)}** | **{total_hours:.0f}h** |")
    lines.append("")
    
    # Success metrics section
    lines.append("## Success Metrics")
    lines.append("")
    lines.append("- [ ] Primary objective completed")
    lines.append("- [ ] Core tasks done")
    lines.append("- [ ] No critical blockers")
    lines.append("- [ ] Documentation updated")
    lines.append("")
    lines.append("*Generated by Goal Decomposer*")
    
    return "\n".join(lines)


def save_goals(epics: list[Epic], goal: str, days: int):
    """Save goals to file."""
    content = format_roadmap(epics, goal, days)
    TASKS_FILE.parent.mkdir(exist_ok=True)
    TASKS_FILE.write_text(content, encoding="utf-8")
    print(f"Saved to: {TASKS_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Goal Decomposer")
    parser.add_argument("command", choices=["decompose", "status", "list"])
    parser.add_argument("text", nargs="?", help="Goal text or task ID")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Timeframe in days")
    parser.add_argument("--output", type=Path, help="Output file path")
    
    args = parser.parse_args()
    
    if args.command == "decompose":
        if not args.text:
            print("Goal text required")
            sys.exit(1)
        
        print(f"Decomposing goal: '{args.text}' ({args.days} days)")
        print("...")
        
        epics = decompose_goal(args.text, args.days)
        roadmap = format_roadmap(epics, args.text, args.days)
        
        if args.output:
            args.output.parent.mkdir(exist_ok=True)
            args.output.write_text(roadmap, encoding="utf-8")
            print(f"Output saved to: {args.output}")
        else:
            print(roadmap)
        
        save_goals(epics, args.text, args.days)
        
    elif args.command == "status":
        print("Status tracking not yet implemented")
        print(f"Tasks file: {TASKS_FILE}")
    
    elif args.command == "list":
        if TASKS_FILE.exists():
            print(TASKS_FILE.read_text(encoding="utf-8"))
        else:
            print("No goals found. Run 'decompose' first.")


if __name__ == "__main__":
    main()
