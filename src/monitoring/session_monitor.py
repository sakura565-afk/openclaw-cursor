"""
OpenClaw Session Monitor
Monitors session sizes and alerts when threshold exceeded.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Default threshold (% of context used)
DEFAULT_THRESHOLD_PCT = 75
LOG_PATH = Path("C:/Users/user/.openclaw/workspace/memory")

def get_session_sizes():
    """Parse openclaw status JSON to get session info."""
    try:
        result = subprocess.run(
            ["npx", "openclaw", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=True
        )
        if result.returncode != 0:
            return None
        
        data = json.loads(result.stdout)
        sessions = []
        
        # Get recent sessions from the JSON
        recent = data.get("sessions", {}).get("recent", [])
        
        for session in recent:
            key = session.get("key", "")
            percent = session.get("percentUsed")
            total_tokens = session.get("totalTokens")
            context_tokens = session.get("contextTokens", 204800)
            
            if percent is not None:
                sessions.append({
                    "key": key,
                    "percent": percent,
                    "totalTokens": total_tokens,
                    "contextTokens": context_tokens
                })
        
        return sessions
    except Exception as e:
        print(f"Error getting session sizes: {e}")
        return None

def log_warning(message: str):
    """Log warning to daily memory file."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_PATH / f"{today}.md"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if log_file.exists():
        content = log_file.read_text(encoding="utf-8")
    else:
        content = f"# {today} -- Daily Log\n\n"
    
    content += f"\n## {timestamp} -- Session Monitor\n{message}\n"
    log_file.write_text(content, encoding="utf-8")

def check_sessions(threshold_pct: float = DEFAULT_THRESHOLD_PCT):
    """Check all sessions and alert on large ones."""
    sessions = get_session_sizes()
    
    if sessions is None:
        print("Failed to get session data")
        return False
    
    alerts = []
    print(f"Checking {len(sessions)} sessions (threshold: {threshold_pct}%)...")
    
    for session in sessions:
        key = session["key"]
        percent = session.get("percent", 0)
        tokens = session.get("totalTokens", 0)
        
        # Check agent:tasks specifically
        if "agent:tasks" in key and percent and percent > threshold_pct:
            alerts.append(f"[WARN] {key}: {percent}% ({tokens} tokens)")
            print(f"  [WARN] {key[:50]:50} {percent or 0:5}% ({tokens or 0} tokens)")
        else:
            status = "[OK]"
            print(f"  {status} {key[:50]:50} {percent or 0:5}% ({tokens or 0} tokens)")
    
    if alerts:
        msg = "## Session Overflow Warning\n" + "\n".join(alerts)
        print("\n" + msg)
        log_warning(msg)
        return True
    
    print(f"\n[OK] All sessions below threshold ({threshold_pct}%)")
    return False

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="OpenClaw Session Monitor")
    parser.add_argument("action", nargs="?", default="check", 
                       choices=["check", "status"])
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_PCT,
                       help=f"Percentage threshold (default: {DEFAULT_THRESHOLD_PCT})")
    
    args = parser.parse_args()
    
    if args.action == "status":
        sessions = get_session_sizes()
        if sessions:
            print(json.dumps(sessions, indent=2))
        return
    
    # Default: check
    exceeded = check_sessions(args.threshold)
    sys.exit(0 if not exceeded else 1)

if __name__ == "__main__":
    main()