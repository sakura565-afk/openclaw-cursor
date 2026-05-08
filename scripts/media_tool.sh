#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON:-python3}"

usage() {
  cat <<'EOF'
Usage:
  scripts/media_tool.sh resize INPUT OUTPUT [--quality 85]
  scripts/media_tool.sh thumb INPUT OUTPUT [--size 300]
  scripts/media_tool.sh compress INPUT OUTPUT [--quality 85]
  scripts/media_tool.sh convert INPUT OUTPUT --format {jpeg,png,webp} [--quality 85]
  scripts/media_tool.sh batch OUTPUT_DIR [INPUT ...] [--operation compress] [--format jpeg]

Environment:
  PYTHON  Override the Python executable used to run media_tool.py
EOF
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

case "$1" in
  resize|thumb|compress|convert|batch)
    COMMAND="$1"
    shift
    exec "$PYTHON_BIN" "$SCRIPT_DIR/media_tool.py" "$COMMAND" "$@"
    ;;
  telegram)
    shift
    exec "$PYTHON_BIN" "$SCRIPT_DIR/media_tool.py" resize "$@"
    ;;
  batch-compress)
    shift
    exec "$PYTHON_BIN" "$SCRIPT_DIR/media_tool.py" batch "$@" --operation compress
    ;;
  batch-thumb)
    shift
    exec "$PYTHON_BIN" "$SCRIPT_DIR/media_tool.py" batch "$@" --operation thumb
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $1" >&2
    usage >&2
    exit 1
    ;;
esac
