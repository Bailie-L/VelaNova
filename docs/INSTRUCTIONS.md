# VelaNova — Project Instructions & Implementation Strategy (v1)

**Status:** Approved  
**Owner:** Bailie  
**Guide:** GPT-5 (single-step discipline)  
**Home (SSD):** `~/Projects/VelaNova`  
**Archives/Backups (HDD):** `/mnt/sata_backups/VelaNova`  
**Wake phrase (default):** “Wake VelaNova.”  
**Also supported:** “VelaNova”, “Hey VelaNova”  
**Networking:** Offline-first. Browsing/scraping allowed in v1 only under constraints (§7).

---

## 1) Ground Rules (Single-Step Discipline)

- **One executable step per assistant reply.** No compounded commands. No `&&` or `;`.
- **System stability > speed.** Prefer reversible changes and read-only inspection before writes.
- **No scope creep.** New ideas go to **v2 Backlog**; v1 stays minimal and stable.
- **Plain English + one-liners.** Each step starts with a short layman explanation, then the single command.
- **Evidence-first.** After each step, capture proof (terminal output, file paths, version strings).

---

## 2) V1 Scope (what we are building now)

- **Local LLM runtime** with a clean chat UI (offline-capable).
- **Always-on voice loop:** wake word → STT → LLM → TTS (all local).
- **Long-term memory:** local embeddings + retrieval.
- **Developer help:** editor integration for code assistance (fully local).
- **Web browsing/scraping:** included *under constraints* (§7). Research on demand; offline-first otherwise.

**Non-Goals for v1:** Cloud APIs, telemetry, dashboards, smart-home control, or auto-agents that execute system commands without confirmation (moved to **v2 Backlog**).

---

## 3) Architecture (high level)

- **Model runner + UI:** Local model server (e.g., Ollama) with minimal web UI.  
- **Voice services:**  
  - Wake word engine (supports multiple phrases).  
  - STT: local Whisper (real-time build).  
  - TTS: low-latency local voices.  
- **Orchestrator:** small service stitches wake→STT→LLM→TTS, writes logs, calls memory.  
- **Memory store:** local vector DB (embeddings + metadata).  
- **Editor bridge:** VS Code assistant pointed at local models.

> **Performance layout:** Runtime + models on SSD; archives & snapshots on HDD.

---

## 4) Directory Layout

~/Projects/VelaNova/ # SSD (runtime)
├─ docs/ # Project docs for humans
│ ├─ INSTRUCTIONS.md # How to interact with GPT (this policy)
│ ├─ IMPLEMENTATION_PLAN.md # Phase gates & acceptance checks
│ └─ OPERATIONS.md # Start/stop/update, backup, troubleshoot
├─ compose/ # Container or service manifests
│ └─ docker-compose.yml # (if containers)
├─ config/ # Wake/STT/TTS/LLM/orchestrator configs
├─ models/ # Local model weights (LLM, STT, TTS)
├─ memory/ # Vector DB + metadata
├─ logs/ # Rotated logs (voice loop, orchestrator)
└─ orchestrator/ # The small process that wires components

/mnt/sata_backups/VelaNova/ # HDD (archives)
├─ snapshots/ # Dated tarballs of configs/models
├─ logs/ # Archived logs (compressed)
└─ research/ # Saved web snapshots & citations

pgsql
Copy code

---

## 5) Phase Plan (Implementation Strategy)

### Phase A — Foundations
**Goal:** Lock decisions; create folders & empty docs; confirm storage layout.  
**Acceptance:** Directory tree exists; docs scaffold present; HDD mounted; permissions verified.

### Phase B — Core Services
**Goal:** Bring up model runtime + chat UI; verify local inference works offline.  
**Acceptance:** Local model loads; Q&A in UI works with no network.

### Phase C — Voice Loop
**Goal:** Wake word → STT → LLM → TTS end-to-end with two wake phrases.  
**Acceptance:** Say “Wake VelaNova” or “Hey VelaNova”; spoken reply; no cloud calls; CPU/GPU within limits.

### Phase D — Memory
**Goal:** Persist conversations and enable retrieval-augmented prompts.  
**Acceptance:** After restart, VelaNova recalls a prior fact accurately.

### Phase E — Dev Mode
**Goal:** Editor integration for local code assistance.  
**Acceptance:** Inline help works; no external API calls.

> **Rollback policy:** Each phase is reversible by removing the new service and restoring the last HDD snapshot.

---

## 6) Operational Guardrails

- **Offline-first:** All components run with outbound network disabled by default.  
- **Resource caps:** Prefer 7B/8B models in 4-bit quant to fit 8 GB VRAM; avoid stalls.  
- **Logging:** Voice loop & orchestrator logs rotate daily; PII stays local.  
- **Security:** Files chmod’d to user; no public-facing daemons.  
- **Backups:** Configs/models snapshotted to HDD on explicit command (see OPERATIONS.md).

---

## 7) Web Browsing/Scraping (v1 constraints)

- **Consent:** Only when Bailie explicitly asks (“browse” / “scrape”).  
- **Scope:** Public pages; respect `robots.txt`; store HTML/PDF to `/mnt/sata_backups/VelaNova/research/DATE/` with a simple `citation.txt`.  
- **Privacy:** No logins/cookies tied to personal accounts.  
- **Rate/Footprint:** Minimal; single-pass snapshot; depth=1 unless asked.

---

## 8) Interaction Protocol for GPT (how we’ll work)

- **You say:** A single request.  
- **Assistant replies:**  
  1) **Layman explanation** (one short line).  
  2) **One executable action** *or* a read-only inspection.  
  3) **Expected evidence** to paste back.  
- **You respond:** “done” with the output (or the error).  
- **Assistant proceeds** only after evidence.

**Formatting rules:**  
- Commands in fenced code blocks (`bash`).  
- Never bundle multiple commands in one block.  
- No destructive ops without an explicit backup step first.

---

## 9) v2 Backlog (parking lot)

Cloud connectors, smart-home integrations, autonomous agents, multi-user profiles, multilingual voices, UI dashboards, mobile companion app, scheduled tasks, and advanced RAG over private corpora.

---

## 10) Naming & Phrases

- **Assistant name:** VelaNova  
- **Default wake phrase:** “Wake VelaNova.”  
- **Also supported:** “VelaNova”, “Hey VelaNova”  
- **Project slug:** `VelaNova` (no hyphen)

---

## 11) Approvals & Versioning

- This document is **v1**. Change requests are logged at the top with date/time and summarized in `docs/INSTRUCTIONS.md`.  
- Once approved, we freeze v1 until Phase A is complete.
- 2025-09-23 — Phase D (Memory): ACCEPTED • snapshot: phaseD-20250923T125243.tar.gz • sha256: e14021f0c8a09da7584c0843da568fbc03bb3a7a66cdc4cd2db3d0a23a77aad5
- 2025-09-24 — Phase F (Hardening): ACCEPTED • snapshot: VelaNova-20250924T080242Z.tgz • sha256: 05df334634859472eb6c0f70b155a2ea25178e50670e5ab706f916ce17d75706

2025-10-09 --- Phase C (Voice Loop Integration): ACCEPTED • snapshot: VelaNova-20251009T111146Z.tgz • sha256: 0d9f5d1cf22943976f9939268807ec75d1ad06683a21e7282e3a718f62213b97
- 2025-10-28 — Phase E (Dev Ergonomics): ACCEPTED • snapshot: VelaNova-20251028T061258Z-phase-e-verified.tgz • sha256: f6a040ea26cf04a1255fdef88248fc697dbe987112da3fda9eaccdfce9858c67

2025-10-28 --- Phase F (Hardening): VERIFIED & OPTIMAL • snapshot VelaNova-20251028T142358Z-phase-f-verified.tgz • sha256 ffefa0d0cb2501b391d2d1b05a45c67c282b671c66fb36db09e2f2331663cfaa

2025-11-04 --- Phase F (Hardening • Offline Parity): COMPLETE ✅ • snapshot VelaNova-20251104T072000Z-phase-f-complete.tgz • sha256 e577b9c5de438aa699cb174c30257b75f666184dd507aa559d9ec5689d1acebd
