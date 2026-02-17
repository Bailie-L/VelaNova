#!/usr/bin/env bash
set -Eeuo pipefail

# VelaNova audit (read-only): collects system, project, docker, audio, and logs into a Markdown report.

BASE="${1:-$HOME/Projects/VelaNova}"
if [[ ! -d "$BASE" ]]; then
  echo "Base directory not found: $BASE" >&2
  exit 1
fi

umask 077

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUTDIR="$BASE/docs/audits"
OUTFILE="$OUTDIR/AUDIT-$TS.md"
TMP="$(mktemp)"
mkdir -p "$OUTDIR"

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

header() {
  printf -- "\n## %s\n\n" "$1" >>"$OUTFILE"
}

run() {
  local title="$1"; shift
  header "$title"
  if "$@" >"$TMP" 2>&1; then
    printf -- '```\n' >>"$OUTFILE"
    cat "$TMP" >>"$OUTFILE"
    printf -- '```\n\n' >>"$OUTFILE"
  else
    printf -- '_Command failed: %s_\n' "$*" >>"$OUTFILE"
    printf -- '```\n' >>"$OUTFILE"
    cat "$TMP" >>"$OUTFILE"
    printf -- '```\n\n' >>"$OUTFILE"
  fi
}

# Title / intro
printf -- '# VelaNova Audit — %s (UTC)\n\n' "$TS" >"$OUTFILE"
printf -- '- Base: %s\n- Host: %s\n- User: %s\n\n' \
  "$BASE" "$(uname -a)" "${USER:-unknown}" >>"$OUTFILE"

run "OS / Kernel / GPU / Tooling" bash -lc '
  echo "OS:"
  if command -v lsb_release >/dev/null 2>&1; then lsb_release -a; else cat /etc/os-release; fi
  echo; echo "Kernel:"; uname -sr
  echo; echo "GPU:"
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi -L
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
  else
    echo "nvidia-smi not found"
  fi
  echo; echo "Python:"; python3 --version 2>&1 || true
  echo; echo "Docker:"; (docker --version || true); (docker compose version || true)
  echo; echo "NVIDIA Container Toolkit:"; (nvidia-container-cli -V || true)
'

run "Project tree (depth 3)" bash -lc '
  if command -v tree >/dev/null 2>&1; then
    tree -L 3 "'"$BASE"'"
  else
    find "'"$BASE"'" -maxdepth 3 -type d -printf "%p\n"
  fi
'

run "Key files present" bash -lc '
  for f in orchestrator/voice_loop.py config/settings.yaml compose/docker-compose.yml docs/SNAPSHOTS.md; do
    p="'"$BASE"'/$f"
    if [[ -e "$p" ]]; then
      sz="$( (stat -c%s "$p" 2>/dev/null) || (stat -f%z "$p" 2>/dev/null) || echo "?" )"
      printf -- "[OK] %s (%s bytes)\n" "$f" "$sz"
    else
      printf -- "[MISS] %s\n" "$f"
    fi
  done
'

run "Python virtualenv packages (.venv)" bash -lc '
  VENV="'"$BASE"'/.venv"
  if [[ -x "$VENV/bin/python" ]]; then
    "$VENV/bin/python" -V
    "$VENV/bin/python" -m pip list --format=columns | egrep -i "(whisper|ctranslate|openwake|piper|sound|torch|onnx|nomic|chroma|chromadb|faiss|numba|scipy|cuda)" || true
  else
    echo ".venv not found"
  fi
'

run "Listening ports (Jarvis-related)" bash -lc '
  (ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null || true) | egrep ":11434|:3000|:10200|:10300|:10400" || true
'

run "Docker Compose status" bash -lc '
  COMPOSE="'"$BASE"'/compose/docker-compose.yml"
  if [[ -f "$COMPOSE" ]]; then
    docker compose -f "$COMPOSE" ps
  else
    echo "compose/docker-compose.yml not found"
  fi
'

run "Ollama models available" bash -lc '
  if command -v ollama >/dev/null 2>&1; then
    echo "[host ollama]"; ollama list || true
  fi
  if docker ps --format "{{.Names}}" | grep -q "^ollama$"; then
    echo; echo "[container ollama]"; docker exec ollama ollama list || true
  fi
'

run "Audio devices (ALSA/Pulse)" bash -lc '
  (arecord -l 2>/dev/null || true)
  echo
  (pactl list short sources 2>/dev/null || true)
'

run "Latest orchestrator logs" bash -lc '
  LATEST="$(ls -1t "'"$BASE"'/logs/voice_loop-"*.log 2>/dev/null | head -n1 || true)"
  if [[ -n "$LATEST" ]]; then
    echo "Showing: $LATEST"
    tail -n 200 "$LATEST"
  else
    echo "No logs found"
  fi
'

run "settings.yaml snapshot (keys only, redacted)" bash -lc '
  if [[ -f "'"$BASE"'/config/settings.yaml" ]]; then
    sed -E "s/(api_key|token|password):.*/\1: [redacted]/" "'"$BASE"'/config/settings.yaml"
  else
    echo "config/settings.yaml not found"
  fi
'

header "Summary checklist"
cat >>"$OUTFILE" <<'EOF'
- [ ] All containers healthy (docker compose ps)
- [ ] Wake word active (no false triggers in quiet room)
- [ ] STT on GPU (faster-whisper) confirmed in logs
- [ ] TTS voice OK (Piper running, voice selected)
- [ ] LLM models present in Ollama (deepseek-r1:7b, qwen2.5-coder:7b)
- [ ] Orchestrator round-trip < 2.5s median
- [ ] Memory DB directory present (.chroma)
EOF

echo "Audit written to: $OUTFILE"
