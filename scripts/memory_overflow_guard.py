"""
Memory Overflow Guard — сжимает сессию при превышении порога токенов.
"""
import os, json, re, logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

MEMORY_PATH = Path.home() / '.openclaw' / 'workspace' / 'MEMORY.md'
SESSION_DIR = Path.home() / '.openclaw' / 'sessions'
TOKEN_THRESHOLD = int(os.getenv('TOKEN_THRESHOLD', '50000'))

def estimate_tokens(text: str) -> int:
    return len(text) // 4

def extract_key_info(messages: list) -> list:
    """Извлекает ключевые решения, договорённости, факты."""
    patterns = [
        (r'(?:решили|договорились|решение:|решено:)\s*(.{10,200})', 'DECISION'),
        (r'(?:важно|помнить|запомнить|зафиксировать:)\s*(.{10,200})', 'FACT'),
        (r'(?:TODO|NEXT|следующий шаг):\s*(.{10,200})', 'TODO'),
        (r'#\w+\s+(.{10,100})', 'TOPIC'),
    ]
    results = []
    full_text = '\n'.join(messages)
    for msg in messages:
        text = msg.get('content', '') or ''
        for pattern, label in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                snippet = match.group(1).strip()
                if len(snippet) > 20:
                    results.append(f"[{label}] {snippet}")
    seen = set()
    unique = []
    for r in results:
        if r not in seen:
            seen.add(r); unique.append(r)
    return unique[:20]

def get_latest_session() -> str:
    if not SESSION_DIR.exists():
        return ''
    sessions = sorted(SESSION_DIR.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    if sessions:
        return sessions[0].read_text(encoding='utf-8')
    return ''

def main():
    load_dotenv()
    log.info("Checking session size...")
    
    session_data = get_latest_session()
    if not session_data:
        log.info("No session found"); return
    
    tokens = estimate_tokens(session_data)
    log.info(f"Session tokens estimate: {tokens}")
    
    if tokens < TOKEN_THRESHOLD:
        log.info(f"Below threshold ({tokens} < {TOKEN_THRESHOLD}), skipping")
        return
    
    log.warning(f"Session overflow! {tokens} tokens > {TOKEN_THRESHOLD}")
    
    # Parse messages
    try:
        session_json = json.loads(session_data)
        messages = []
        for msg in session_json.get('messages', []):
            role = msg.get('role', '')
            content = msg.get('content', '')
            if role in ('user', 'assistant') and content:
                messages.append(f"[{role.upper()}] {content[:500]}")
    except:
        messages = [session_data[:2000]]
    
    key_info = extract_key_info(messages)
    
    if not key_info:
        log.info("No key info extracted"); return
    
    # Append to MEMORY.md
    if not MEMORY_PATH.exists():
        MEMORY_PATH.write_text("# MEMORY\n\n")
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    entry = f"\n## Session Overflow {timestamp} ({tokens} tokens)\n"
    entry += '\n'.join(f"- {info}" for info in key_info)
    entry += '\n'
    
    with open(MEMORY_PATH, 'a', encoding='utf-8') as f:
        f.write(entry)
    
    log.info(f"Saved {len(key_info)} key items to MEMORY.md")

if __name__ == '__main__':
    main()