#!/usr/bin/env python3
"""
Auto Memory Cleanup — clean and maintain MEMORY.md.
Removes stale entries, merges duplicates, keeps only relevant facts.

Usage:
    python -m scripts.auto_memory_cleanup           # Interactive analysis
    python -m scripts.auto_memory_cleanup --auto    # Auto clean (safe mode)
    python -m scripts.auto_memory_cleanup --dry-run # Preview without changes
    python -m scripts.auto_memory_cleanup --analyze # Show stats only
"""

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_MEMORY = Path.home() / ".openclaw" / "workspace" / "MEMORY.md"
DAILY_DIR = Path.home() / ".openclaw" / "workspace" / "memory"
CUTOFF_DAYS = 90  # Default: consider entries older than 90 days stale


class MemoryCleaner:
    """Analyze and clean MEMORY.md."""
    
    def __init__(self, memory_path: Path, dry_run: bool = False):
        self.memory_path = memory_path
        self.dry_run = dry_run
        self.original_content = ""
        self.lines: list[str] = []
        self.stats: dict = {}
        
    def load(self) -> bool:
        """Load MEMORY.md content."""
        if not self.memory_path.exists():
            print(f"ERROR: MEMORY.md not found: {self.memory_path}")
            return False
        
        self.original_content = self.memory_path.read_text(encoding="utf-8")
        self.lines = self.original_content.splitlines()
        return True
    
    def find_sections(self) -> list[tuple[int, str]]:
        """Find all ## sections with line numbers."""
        sections = []
        for i, line in enumerate(self.lines):
            if re.match(r"^##\s+", line):
                sections.append((i, line.strip()))
        return sections
    
    def analyze(self) -> dict:
        """Analyze MEMORY.md and return statistics."""
        stats = {
            "total_lines": len(self.lines),
            "total_sections": 0,
            "section_names": [],
            "daily_notes_count": 0,
            "old_entries": 0,
            "potential_duplicates": [],
            "last_modified": datetime.fromtimestamp(
                self.memory_path.stat().st_mtime
            ).isoformat()
        }
        
        sections = self.find_sections()
        stats["total_sections"] = len(sections)
        stats["section_names"] = [s[1] for s in sections]
        
        for i, line in enumerate(self.lines):
            # Count daily notes sections
            if "## Daily Notes" in line or "## Log" in line:
                stats["daily_notes_count"] += 1
            
            # Check for old dates (older than CUTOFF_DAYS)
            date_pattern = r"\d{4}-\d{2}-\d{2}"
            dates = re.findall(date_pattern, line)
            for date_str in dates:
                try:
                    date = datetime.strptime(date_str, "%Y-%m-%d")
                    age = (datetime.now() - date).days
                    if age > CUTOFF_DAYS:
                        stats["old_entries"] += 1
                except ValueError:
                    pass
        
        # Find potential duplicate sections
        seen = {}
        for name in stats["section_names"]:
            normalized = name.lower().strip()
            if normalized in seen:
                stats["potential_duplicates"].append({
                    "original": seen[normalized],
                    "duplicate": name
                })
            else:
                seen[normalized] = name
        
        self.stats = stats
        return stats
    
    def print_analysis(self):
        """Print memory analysis."""
        stats = self.analyze()
        
        print("\n" + "="*60)
        print("MEMORY.md ANALYSIS")
        print("="*60)
        print(f"File: {self.memory_path}")
        print(f"Last modified: {stats['last_modified'][:10]}")
        print(f"Total lines: {stats['total_lines']}")
        print(f"Sections: {stats['total_sections']}")
        print(f"Daily notes sections: {stats['daily_notes_count']}")
        print(f"Old entries (>90 days): {stats['old_entries']}")
        
        if stats["section_names"]:
            print("\nSections found:")
            for name in stats["section_names"]:
                print(f"  • {name}")
        
        if stats["potential_duplicates"]:
            print("\n⚠️ Potential duplicates:")
            for dup in stats["potential_duplicates"]:
                print(f"  • '{dup['original']}' vs '{dup['duplicate']}'")
        
        print()
    
    def clean_old_daily_notes(self, cutoff_days: int = CUTOFF_DAYS) -> int:
        """Remove daily notes older than cutoff_days."""
        if "Daily Notes" not in [s[1] for s in self.find_sections()]:
            print("No Daily Notes section found, skipping cleanup")
            return 0
        
        removed = 0
        sections = self.find_sections()
        
        for idx, (line_idx, section_name) in enumerate(sections):
            if "Daily Notes" not in section_name:
                continue
            
            # Get end of this section
            end_idx = len(self.lines)
            if idx + 1 < len(sections):
                end_idx = sections[idx + 1][0]
            
            # Process lines in this section
            section_lines = self.lines[line_idx:end_idx]
            new_section_lines = []
            
            cutoff_date = datetime.now() - timedelta(days=cutoff_days)
            
            for line in section_lines:
                # Check for dates in list items
                date_pattern = r"(\d{4}-\d{2}-\d{2})"
                match = re.search(date_pattern, line)
                
                if match and line.strip().startswith("-"):
                    date_str = match.group(1)
                    try:
                        entry_date = datetime.strptime(date_str, "%Y-%m-%d")
                        age = (datetime.now() - entry_date).days
                        
                        if age > cutoff_days:
                            removed += 1
                            continue  # Skip old entry
                    except ValueError:
                        pass
                
                new_section_lines.append(line)
            
            # Replace section
            self.lines[line_idx:end_idx] = new_section_lines
        
        return removed
    
    def merge_duplicate_sections(self) -> int:
        """Merge sections with similar names."""
        merged = 0
        sections = self.find_sections()
        
        # Find duplicates
        name_to_idx = {}
        to_remove = []
        
        for idx, (_, name) in enumerate(sections):
            normalized = re.sub(r"[^a-zA-Z0-9]", "", name.lower())
            
            if normalized in name_to_idx:
                # Found duplicate
                orig_idx = name_to_idx[normalized]
                to_remove.append(idx)
                merged += 1
                print(f"  Merging duplicate: '{name}' -> '{sections[orig_idx][1]}'")
            else:
                name_to_idx[normalized] = idx
        
        # Remove duplicates (in reverse order to maintain indices)
        for idx in reversed(to_remove):
            line_idx = sections[idx][0]
            # Find section end
            end_idx = len(self.lines)
            if idx + 1 < len(sections):
                end_idx = sections[idx + 1][0]
            
            # Delete section (keep only first occurrence)
            del self.lines[line_idx:end_idx]
        
        return merged
    
    def save(self) -> bool:
        """Save cleaned content."""
        if self.dry_run:
            print("\n[DRY RUN] No changes written")
            return True
        
        # Backup original
        backup_path = self.memory_path.with_suffix(".md.bak")
        backup_path.write_text(self.original_content, encoding="utf-8")
        print(f"Backup saved: {backup_path}")
        
        # Save cleaned version
        new_content = "\n".join(self.lines)
        self.memory_path.write_text(new_content, encoding="utf-8")
        print(f"Cleaned MEMORY.md saved")
        
        return True
    
    def run_cleanup(self, cutoff_days: int, merge_dups: bool = True) -> dict:
        """Run full cleanup and return stats."""
        results = {
            "old_entries_removed": 0,
            "duplicates_merged": 0,
            "lines_changed": 0
        }
        
        if merge_dups:
            results["duplicates_merged"] = self.merge_duplicate_sections()
        
        results["old_entries_removed"] = self.clean_old_daily_notes(cutoff_days)
        results["lines_changed"] = len(self.lines)
        
        return results


def main():
    parser = argparse.ArgumentParser(description="Auto Memory Cleanup for MEMORY.md")
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY,
                        help=f"MEMORY.md path (default: {DEFAULT_MEMORY})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing")
    parser.add_argument("--auto", action="store_true",
                        help="Run automatic cleanup (safe mode)")
    parser.add_argument("--analyze", action="store_true",
                        help="Show analysis only")
    parser.add_argument("--cutoff", type=int, default=CUTOFF_DAYS,
                        help=f"Remove entries older than N days (default: {CUTOFF_DAYS})")
    parser.add_argument("--no-merge", action="store_true",
                        help="Skip duplicate section merging")
    
    args = parser.parse_args()
    
    cleaner = MemoryCleaner(args.memory, dry_run=args.dry_run)
    
    if not cleaner.load():
        sys.exit(1)
    
    if args.analyze:
        cleaner.print_analysis()
        return
    
    if args.auto or args.dry_run:
        print("\n" + "="*60)
        print("MEMORY CLEANUP")
        print("="*60)
        
        cleaner.print_analysis()
        
        print("\nRunning cleanup...")
        results = cleaner.run_cleanup(
            cutoff_days=args.cutoff,
            merge_dups=not args.no_merge
        )
        
        print(f"\nCleanup results:")
        print(f"  Old entries removed: {results['old_entries_removed']}")
        print(f"  Duplicates merged: {results['duplicates_merged']}")
        
        cleaner.save()
    
    print()


if __name__ == "__main__":
    main()
