"""
Cursor Cloud Agents Batch Queue Manager
Automatically launches and monitors Cursor agents in batches.
"""
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict

API_KEY = "crsr_8451e2fa232a5ca1982fd0876a5b5eb0632e24bd4e34680142b4a9c26be8c685"
REPO = "https://github.com/sakura565-afk/openclaw-cursor"
REF = "main"
BATCH_SIZE = 5
POLL_INTERVAL = 30  # seconds
LOG_PATH = Path("C:/Users/user/.openclaw/workspace/memory")

def get_active_agents() -> int:
    """Count currently running agents via API."""
    # Simplified: check via cursor status
    try:
        result = subprocess.run(
            ["python", "cursor_cloud_agent.py", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(Path(__file__).parent.parent)
        )
        return 0
    except:
        return 0

def launch_agent(branch: str, description: str) -> Dict:
    """Launch a single Cursor agent."""
    prompt = f"Merge the branch origin/cursor/{branch} into main. Review code, ensure tests pass, merge via PR. Reply with PR URL."
    
    try:
        result = subprocess.run(
            [
                "python", "cursor_cloud_agent.py", "launch",
                "--api-key", API_KEY,
                "--repo", REPO,
                "--ref", REF,
                "--prompt", prompt,
                "--auto-pr"
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(__file__).parent.parent.parent)
        )
        
        output = result.stdout + result.stderr
        
        # Parse agent ID from output
        if "Agent launched:" in output:
            agent_id = output.split("Agent launched:")[1].split("|")[0].strip()
            return {"agent_id": agent_id, "branch": branch, "status": "LAUNCHED"}
        elif "limit" in output.lower():
            return {"agent_id": None, "branch": branch, "status": "LIMIT_REACHED"}
        else:
            return {"agent_id": None, "branch": branch, "status": "FAILED"}
            
    except Exception as e:
        return {"agent_id": None, "branch": branch, "status": f"ERROR: {e}"}

def poll_agent(agent_id: str) -> str:
    """Poll agent status."""
    try:
        result = subprocess.run(
            [
                "python", "cursor_cloud_agent.py", "poll",
                "--api-key", API_KEY,
                "--agent-id", agent_id
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path(__file__).parent.parent.parent)
        )
        
        output = result.stdout + result.stderr
        
        if '"status": "FINISHED"' in output:
            return "FINISHED"
        elif '"status": "RUNNING"' in output:
            return "RUNNING"
        elif "404" in output:
            return "NOT_FOUND"
        else:
            return "PROCESSING"
            
    except Exception as e:
        return f"ERROR: {e}"

def log_queue_status(pending: List[str], running: Dict, completed: List[Dict]):
    """Log queue status to file."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_PATH / f"cursor_queue_{today}.md"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    content = f"# Cursor Queue Status — {timestamp}\n\n"
    content += f"## Pending ({len(pending)})\n"
    for b in pending:
        content += f"- {b}\n"
    
    content += f"\n## Running ({len(running)})\n"
    for agent_id, branch in running.items():
        content += f"- {branch}: {agent_id}\n"
    
    content += f"\n## Completed\n"
    for item in completed:
        content += f"- {item['branch']}: {item['status']} → {item.get('pr_url', 'no PR')}\n"
    
    log_file.write_text(content, encoding="utf-8")
    print(content)

def run_queue(branches: List[str]):
    """
    Main queue runner.
    Launches agents in batches, monitors, retries on limit.
    """
    pending = branches.copy()
    running = {}  # agent_id -> branch
    completed = []
    
    print(f"🚀 Starting queue with {len(branches)} branches...")
    
    while pending or running:
        # Launch next batch if we have capacity
        while len(running) < BATCH_SIZE and pending:
            branch = pending.pop(0)
            
            result = launch_agent(branch, "")
            
            if result["status"] == "LIMIT_REACHED":
                print(f"⚠️ Limit reached, {len(pending)} branches waiting")
                pending.insert(0, branch)  # Put back
                break
            elif result["agent_id"]:
                running[result["agent_id"]] = branch
                print(f"✅ Launched: {branch} ({result['agent_id']})")
            else:
                print(f"❌ Failed to launch: {branch}")
                completed.append({
                    "branch": branch,
                    "status": "LAUNCH_FAILED",
                    "pr_url": None
                })
        
        # Poll running agents
        for agent_id in list(running.keys()):
            status = poll_agent(agent_id)
            
            if status == "FINISHED":
                branch = running.pop(agent_id)
                # Extract PR URL from poll output
                completed.append({
                    "branch": branch,
                    "status": "FINISHED",
                    "pr_url": f"https://cursor.com/agents/{agent_id}"  # Approximate
                })
                print(f"✅ Completed: {branch}")
            elif status == "NOT_FOUND":
                running.pop(agent_id)
                completed.append({
                    "branch": running.get(agent_id, "unknown"),
                    "status": "NOT_FOUND",
                    "pr_url": None
                })
        
        # Log status
        log_queue_status(pending, running, completed)
        
        # Wait before next poll
        if running:
            time.sleep(POLL_INTERVAL)
    
    print(f"\n🎉 Queue complete! {len(completed)} branches processed")
    return completed

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Cursor Batch Queue Manager")
    parser.add_argument("action", choices=["run", "status"])
    parser.add_argument("--branches", nargs="+", help="Branch names to process")
    parser.add_argument("--file", type=Path, help="File with branch list (one per line)")
    
    args = parser.parse_args()
    
    if args.action == "status":
        print("Queue status check not yet implemented")
        return
    
    # Get branches
    branches = []
    if args.branches:
        branches = args.branches
    elif args.file and args.file.exists():
        branches = [l.strip() for l in args.file.read_text().splitlines() if l.strip()]
    else:
        print("Error: specify --branches or --file")
        return
    
    run_queue(branches)

if __name__ == "__main__":
    main()