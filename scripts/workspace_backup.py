#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Workspace Backup → Google Drive

Incremental backup: only changed files (SHA256) get uploaded.
Structure on Drive: Backups/YYYY-WW/
Keeps last 8 weeks of backups. Manifest tracks all files per backup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("workspace_backup")

# ── Config from env ─────────────────────────────────────────────────────────

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "")
if not COMPOSIO_API_KEY:
    log.error("COMPOSIO_API_KEY not set")
    raise SystemExit(1)

COMPOSIO_WORKSPACE_ID = os.getenv("COMPOSIO_WORKSPACE_ID", "")
COMPOSIO_ENTITY_ID = os.getenv("COMPOSIO_ENTITY_ID", "")

# ── Paths ───────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]  # openclaw-cursor root
HASH_STORE = REPO_ROOT / ".workspace_backup_hashes.json"
MANIFEST_NAME = "manifest.json"
BACKUPS_FOLDER_NAME = "Backups"
KEEP_WEEKS = 8

# ── File discovery ───────────────────────────────────────────────────────────

def discover_files() -> list[str]:
    """All relevant workspace files: MD, config, scripts, tasks."""
    ws = REPO_ROOT
    found: list[str] = []

    # Root-level files
    for name in (
        "MEMORY.md", "AGENTS.md", "SOUL.md", "IDENTITY.md", "USER.md", "TOOLS.md",
        ".env", ".gitignore", "README.md",
    ):
        p = ws / name
        if p.is_file():
            found.append(name)

    # scripts/ and tasks/ subdirectories
    for sub in ("scripts", "tasks"):
        sub_path = ws / sub
        if sub_path.is_dir():
            for p in sorted(sub_path.iterdir()):
                if p.is_file() and p.suffix in (".py", ".yaml", ".yml", ".sh", ".md"):
                    rel = str(p.relative_to(ws)).replace(os.sep, "/")
                    found.append(rel)

    # memory/ subdirectory
    mem_path = ws / "memory"
    if mem_path.is_dir():
        for p in sorted(mem_path.iterdir()):
            if p.is_file():
                rel = str(p.relative_to(ws)).replace(os.sep, "/")
                found.append(rel)

    return sorted(set(found))


# ── Hashing ─────────────────────────────────────────────────────────────────

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_hash_store() -> dict[str, str]:
    if HASH_STORE.exists():
        try:
            return json.loads(HASH_STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_hash_store(store: dict[str, str]) -> None:
    HASH_STORE.write_text(
        json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def current_week_id() -> str:
    """ISO YYYY-WW format."""
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-{week:02d}"


# ── Composio REST helpers ───────────────────────────────────────────────────

COMPOSIO_BASE = "https://backend.composio.dev"

def _composio_headers() -> dict[str, str]:
    return {
        "x-api-key": COMPOSIO_API_KEY,
        "Content-Type": "application/json",
    }


def _composio_post(endpoint: str, body: dict) -> dict:
    url = f"{COMPOSIO_BASE}{endpoint}"
    resp = requests.post(url, headers=_composio_headers(), json=body, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _composio_get(endpoint: str, params: dict | None = None) -> dict:
    url = f"{COMPOSIO_BASE}{endpoint}"
    resp = requests.get(url, headers=_composio_headers(), params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


# ── Drive operations via Composio REST ──────────────────────────────────────

def _get_active_connection() -> dict | None:
    """Get the active Google Drive connection id."""
    body = {
        "filter": {},
        "limit": 10,
    }
    if COMPOSIO_ENTITY_ID:
        body["entityId"] = COMPOSIO_ENTITY_ID
    try:
        result = _composio_post("/api/v2/oauth/connections/list", body)
        connections = result.get("data", {}).get("connections", [])
        for conn in connections:
            if conn.get("integration", "").lower() in ("googledrive", "google_drive"):
                if conn.get("status", "").upper() == "ACTIVE":
                    return conn
        # fallback: first active connection
        for conn in connections:
            if conn.get("status", "").upper() == "ACTIVE":
                return conn
    except Exception as exc:
        log.warning("Could not list connections: %s", exc)
    return None


def find_folder(name: str, parent_id: str | None = None) -> str | None:
    """Find a Drive folder by name. Returns id or None."""
    body = {
        "query": f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder'",
        "fields": "files(id,name)",
        "includeItemsFromAllDrives": True,
    }
    if parent_id:
        body["query"] += f" and '{parent_id}' in parents"
    try:
        result = _composio_post("/api/v2/integrations/google_drive/search", body)
        files = result.get("data", {}).get("files", [])
        if files:
            return files[0]["id"]
    except Exception as exc:
        log.warning("find_folder('%s') failed: %s", name, exc)
    return None


def create_folder(name: str, parent_id: str | None = None) -> str:
    """Create a Drive folder. Returns the new folder id."""
    body: dict = {"name": name}
    if parent_id:
        body["parent_id"] = parent_id
    result = _composio_post("/api/v2/integrations/google_drive/folder", body)
    data = result.get("data", {})
    fid = data.get("id", "")
    if not fid:
        raise RuntimeError(f"create_folder '{name}' returned no id: {data}")
    return fid


def find_or_create_folder(name: str, parent_id: str | None = None) -> str:
    fid = find_folder(name, parent_id)
    if fid:
        log.info("Found folder '%s': %s", name, fid)
        return fid
    fid = create_folder(name, parent_id)
    log.info("Created folder '%s': %s", name, fid)
    return fid


def upload_file(local_path: Path, folder_id: str, dest_name: str | None = None) -> str:
    """
    Upload a local file to a Drive folder.
    Returns the uploaded file id.
    """
    dest_name = dest_name or local_path.name
    url = f"{COMPOSIO_BASE}/api/v2/integrations/google_drive/file"
    with open(local_path, "rb") as f:
        files = {"file": (dest_name, f)}
        data = {}
        if folder_id:
            data["parent_id"] = folder_id
        if COMPOSIO_ENTITY_ID:
            data["entity_id"] = COMPOSIO_ENTITY_ID
        resp = requests.post(url, headers={"x-api-key": COMPOSIO_API_KEY}, data=data, files=files, timeout=300)
    resp.raise_for_status()
    result = resp.json()
    data = result.get("data", {})
    fid = data.get("id", "")
    if not fid:
        raise RuntimeError(f"upload_file '{dest_name}' returned no id: {data}")
    return fid


def delete_file(file_id: str) -> bool:
    """Delete a file from Drive. Returns True on success."""
    try:
        body: dict = {"file_id": file_id}
        if COMPOSIO_ENTITY_ID:
            body["entity_id"] = COMPOSIO_ENTITY_ID
        _composio_post("/api/v2/integrations/google_drive/delete", body)
        return True
    except Exception as exc:
        log.warning("delete_file('%s') failed: %s", file_id, exc)
        return False


# ── Cleanup ─────────────────────────────────────────────────────────────────

def cleanup_old_backups(keep_weeks: int = 8) -> int:
    """Delete Backups/YYYY-WW folders older than keep_weeks. Returns count deleted."""
    now = datetime.now(timezone.utc)
    current_year, current_week, _ = now.isocalendar()

    # List all folders inside Backups/
    backups_fid = find_folder(BACKUPS_FOLDER_NAME)
    if not backups_fid:
        log.info("No Backups folder found — nothing to clean up.")
        return 0

    deleted = 0

    # Find week subfolders via search across all drives
    body = {
        "query": f"'{backups_fid}' in parents and mimeType = 'application/vnd.google-apps.folder'",
        "fields": "files(id,name,createdTime)",
        "includeItemsFromAllDrives": True,
    }
    try:
        result = _composio_post("/api/v2/integrations/google_drive/search", body)
        folders = result.get("data", {}).get("files", [])
    except Exception as exc:
        log.warning("Could not list backup week folders: %s", exc)
        return 0

    for folder in folders:
        name = folder.get("name", "")
        folder_id = folder.get("id", "")
        if not name or not folder_id:
            continue
        # Only match YYYY-WW pattern
        if "-" not in name:
            continue
        parts = name.split("-")
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        try:
            year, week = int(parts[0]), int(parts[1])
            age_weeks = (current_year - year) * 52 + (current_week - week)
            if age_weeks > keep_weeks:
                log.info("Deleting old backup folder '%s' (age=%d weeks, keep=%d)", name, age_weeks, keep_weeks)
                if delete_file(folder_id):
                    deleted += 1
                else:
                    log.warning("  → failed to delete '%s'", name)
        except Exception as exc:
            log.warning("  → error processing folder '%s': %s", name, exc)

    log.info("Cleanup complete: %d folders deleted", deleted)
    return deleted


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    log.info("Workspace Backup started")
    log.info("Repo root: %s", REPO_ROOT)

    # Verify we have a Drive connection
    conn = _get_active_connection()
    if not conn:
        log.error("No active Google Drive connection found via Composio")
        raise SystemExit(1)
    log.info("Using connection: %s", conn.get("alias", conn.get("id", "?")))

    # Discover files
    files = discover_files()
    log.info("Tracking %d files", len(files))

    if not files:
        log.info("Nothing to back up.")
        return 0

    # Load hash store
    hash_store = load_hash_store()
    now_str = datetime.now(timezone.utc).isoformat()
    week_id = current_week_id()

    # Compute current hashes and find changed files
    changed_files: list[dict] = []
    total_size = 0

    for rel_path in files:
        file_path = REPO_ROOT / rel_path
        if not file_path.is_file():
            continue

        file_hash = sha256(file_path)
        file_size = file_path.stat().st_size
        total_size += file_size
        prev_hash = hash_store.get(rel_path)
        changed = prev_hash != file_hash

        log.info(
            "  %-8s  %s  (%s, %d bytes)",
            "CHANGED" if changed else "unchanged",
            rel_path,
            file_hash[:12],
            file_size,
        )

        changed_files.append({
            "name": rel_path,
            "sha256": file_hash,
            "size": file_size,
            "mtime": datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            "changed": changed,
        })
        hash_store[rel_path] = file_hash

    changed_only = [f for f in changed_files if f["changed"]]
    if not changed_only:
        log.info("No changed files — nothing to upload.")
        save_hash_store(hash_store)
        return 0

    # Ensure Backups root folder
    backups_id = find_or_create_folder(BACKUPS_FOLDER_NAME)

    # Ensure weekly subfolder
    week_id_found = find_or_create_folder(week_id, parent_id=backups_id)

    # Upload changed files
    uploaded: list[dict] = []
    errors: list[dict] = []

    for info in changed_only:
        rel_path = info["name"]
        file_path = REPO_ROOT / rel_path
        if not file_path.is_file():
            continue

        dest_name = rel_path.replace("/", "_").replace("\\", "_")
        try:
            fid = upload_file(file_path, week_id_found, dest_name)
            uploaded.append({
                "file": rel_path,
                "sha256": info["sha256"],
                "size": info["size"],
                "drive_id": fid,
            })
            log.info("  → uploaded '%s' (id=%s)", dest_name, fid)
        except Exception as exc:
            log.error("Upload failed for '%s': %s", rel_path, exc)
            errors.append({"file": rel_path, "error": str(exc)})

    # Write and upload manifest
    manifest = {
        "backup_week": week_id,
        "created_at": now_str,
        "total_tracked": len(changed_files),
        "uploaded_count": len(uploaded),
        "total_size_bytes": total_size,
        "files": changed_files,
        "uploaded": uploaded,
        "errors": errors,
    }

    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.write(fd, json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
    os.close(fd)
    tmp_path = Path(tmp)

    try:
        upload_file(tmp_path, week_id_found, MANIFEST_NAME)
        log.info("Manifest uploaded")
    except Exception as exc:
        log.error("Manifest upload failed: %s", exc)
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    # Cleanup old backups
    try:
        cleanup_old_backups(keep_weeks=KEEP_WEEKS)
    except Exception as exc:
        log.warning("Old backup cleanup skipped: %s", exc)

    # Save updated hash store
    save_hash_store(hash_store)

    # Summary
    log.info("=" * 60)
    log.info("Backup complete: %s", week_id)
    log.info("  Files tracked  : %d", len(changed_files))
    log.info("  Uploaded       : %d", len(uploaded))
    log.info("  Errors         : %d", len(errors))
    log.info("  Total size     : %s bytes", total_size)
    log.info("  Week folder ID : %s", week_id_found)
    log.info("=" * 60)

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
