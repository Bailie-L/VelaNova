# VelaNova — OPERATIONS (v1)

**Mode:** Offline-first. All services run locally with outbound network disabled by default.

---

## 1) Daily Use (Phase B+ overview)
These commands will be filled in during **Phase B** when services are installed. For now, this section is a placeholder header only.
- **Start local chat UI:** _TBD in Phase B_
- **Stop services:** _TBD in Phase B_
- **Status & logs:** _TBD in Phase B_

---

## 2) Logs
- Runtime logs live in: `~/Projects/VelaNova/logs/`
- Archive logs live in: `/mnt/sata_backups/VelaNova/logs/`
- Rotate daily (Phase B will add a simple rotation job).

---

## 3) Backups — Snapshot Policy (HDD)
**Where:** `/mnt/sata_backups/VelaNova/snapshots/`  
**What:** Human docs + configs + compose + models + orchestrator code.  
**When:** 
- **Configs & docs:** after each material change.
- **Models:** after adding or updating model files.
- **Memory store:** optional; weekly or before major upgrades.

### 3.1 Create a snapshot (readable one-liner block)
```bash
SNAPDIR="/mnt/sata_backups/VelaNova/snapshots"; TS="$(date -u +%Y%m%dT%H%M%SZ)"; \
tar -C "$HOME/Projects" -czf "$SNAPDIR/VelaNova-$TS.tgz" \
  VelaNova/docs VelaNova/config VelaNova/compose VelaNova/models VelaNova/orchestrator && \
sha256sum "$SNAPDIR/VelaNova-$TS.tgz" | tee "$SNAPDIR/VelaNova-$TS.tgz.sha256"
Evidence to capture: the .tgz filename and its SHA256 line.

Notes:

Add VelaNova/memory to the tar list if you want to snapshot embeddings/state.

If the archive might exceed free space, check: df -h /mnt/sata_backups.

3.2 Verify a snapshot
bash
Copy code
sha256sum -c "/mnt/sata_backups/VelaNova/snapshots/VelaNova-<TIMESTAMP>.tgz.sha256"
3.3 Restore from a snapshot (reversible)
bash
Copy code
tar -C "$HOME/Projects" -xzf "/mnt/sata_backups/VelaNova/snapshots/VelaNova-<TIMESTAMP>.tgz"
This overwrites files inside ~/Projects/VelaNova/ with the archived versions.

4) Research Snapshots (when browsing is requested)
When you ask to browse/scrape, save artifacts here:

bash
Copy code
/mnt/sata_backups/VelaNova/research/YYYY-MM-DD/
  ├─ page-1.html / paper-1.pdf / ...
  └─ citation.txt   # URLs, access date/time, short description
5) Security
No public-facing ports.

Files owned by the local user.

No cloud API keys stored in this project for v1.

6) Rollback
Stop local services (Phase B will add exact commands).

Pick a verified snapshot from /mnt/sata_backups/VelaNova/snapshots/.

Restore with the command in §3.3.

Start services and validate.

7) Change Control
v1 is frozen once approved. Any change request is logged at the top of docs/INSTRUCTIONS.md with date/time and rationale.

## Phase D (Memory) — Ops Quick Ref • 2025-09-23
- Start loop (foreground):  
  `~/Projects/VelaNova/.venv/bin/python ~/Projects/VelaNova/orchestrator/voice_loop.py`
- Store token (fast-path, speaks token):  
  `VelaNova, store this fact: the codeword is mango-73.`
- Recall (token-only):  
  `VelaNova, what is the codeword?`
- Non-codeword example (store + recall):  
  `VelaNova, store this fact: the project mascot is a dire wolf.`  
  `VelaNova, what is the project mascot?`
- Logs (today):  
  `tail -n 120 ~/Projects/VelaNova/logs/voice_loop-$(date +%Y%m%d).log`
- DB location: `~/Projects/VelaNova/memory/db/memory.sqlite` (WAL).  
- Snapshot template (exclude Ollama keys):  
  `tar -C "$HOME/Projects" --exclude='VelaNova/models/ollama/id_*' -czf /mnt/sata_backups/VelaNova/snapshots/phaseD-$(date +%Y%m%dT%H%M%S).tar.gz VelaNova`

---

## Troubleshooting: Ollama GPU Access Lost

**Symptom:** LLM inference timeouts (>15s) with context, despite GPU showing idle

**Diagnosis:**
```bash
# Test GPU access from inside container
docker exec vela_ollama nvidia-smi
# If shows: "Failed to initialize NVML: Unknown Error" → GPU passthrough broken
```

**Root Cause:** Long-running container (3+ days) may lose GPU access after driver updates or system events

**Fix:**
```bash
# Restart container to restore GPU passthrough
docker restart vela_ollama

# Verify GPU visible
docker exec vela_ollama nvidia-smi

# Verify VRAM usage (should be ~4400 MiB with model loaded)
docker exec vela_ollama nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits
```

**Prevention:** Consider periodic container restarts or use `restart: always` policy (already configured in Phase B)

---
