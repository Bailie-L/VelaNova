VelaNova — Technical Implementation Strategy (Verification-First)
Owner: Bailie
Runtime (SSD): ~/Projects/VelaNova
Archives/Backups (HDD): /mnt/sata_backups/VelaNova
Discipline: One step per reply, evidence-first, offline-by-default.

0) How we operate (non-negotiables)
    • Verification-first: Before starting any phase, run a quick precheck to confirm prior gates are truly green. No assumptions.
    • Offline-first: Outbound network stays blocked by default. Connected Mode only when explicitly toggled.
    • One-step protocol: When we execute, I’ll give one clear purpose line → one command → expected evidence you’ll paste back.
    • Rollback: After each accepted phase, snapshot configs and state to HDD.
Tools already present:
    • tools/audit.sh (read-only auditor; writes docs/audits/AUDIT-*.md).

1) Baseline Phases A–E (verify before you build on them)
Use tools/audit.sh and your existing logs to check each gate. If any item fails, fix it before continuing.
Phase A — Foundations (verify)
    • ~/Projects/VelaNova exists with: docs/, config/, compose/, orchestrator/, logs/, memory/.
    • docs/INSTRUCTIONS.md, docs/IMPLEMENTATION_PLAN.md, docs/OPERATIONS.md exist (at least headers).
    • At least one snapshot on HDD with matching SHA-256 in docs/SNAPSHOTS.md.
Acceptance: Folder tree intact, docs present, snapshot verified.
Phase B — Core Services (verify)
    • Docker Compose stack up: Ollama on :11434, Open WebUI on :3000, both healthy.
    • Compose has no schema/version warnings (we’ll fix if present).
    • Egress is blocked by default (documented toggle in OPERATIONS).
Acceptance: compose ps shows healthy; offline stance confirmed.
Phase C — Voice Loop (verify)
    • Orchestrator runs end-to-end: wake → STT → LLM → TTS → spoken reply.
    • Wake path declared: hard KWS or soft STT-wake (no silent failures).
    • STT ready messages in logs, and TTS voice plays without underruns.
Acceptance: 3 successful voice turns in a row; no crash; logs saved.
Phase D — Memory (verify)
    • Conversational turns persist locally (SQLite or Chroma).
    • Query or recall works after process restart.
Acceptance: Ask for a detail from a prior session; system recalls.
Phase E — Dev Ergonomics (verify)
    • Editor helper (e.g., Continue) can talk to local models.
    • Orchestrator can switch profiles (general ↔ coder) when requested.
Acceptance: One coder task completes offline (no outbound calls).

2) Fix-Forwards & Enhancements (F–J)
Each phase starts with a Precheck (what must be true) and ends with Acceptance (evidence you’ll capture).
Phase F — Hardening (Offline parity)
Precheck: A–E verified.
Objectives:
    1. Align wake models with config;
    2. Move STT to CUDA (fast);
    3. Ensure LLM models are present and persistent;
    4. Clean Compose warnings;
    5. Log timing for latency budgets.
Changes:
    • Wake models: If your config points to *.onnx but disk has *.tflite, update the config to match (or install ONNX KWS pack). Keep phrases: “VelaNova”, “Hey VelaNova”.
    • STT (CUDA): Use Faster-Whisper via CTranslate2 on cuda with compute_type: int8_float16 and a small model to fit 8 GB VRAM.
    • LLM tags: Ensure at least one chat model is present (e.g., llama3.2:3b or your chosen default) and persists across container restarts.
    • Compose hygiene: Remove obsolete version: and any no-op keys; keep ports/volumes stable.
    • Latency logs: Add per-turn measurements (wake detect, STT duration, LLM think, TTS TTFA, total).
Acceptance (capture logs):
    • Wake triggers reliably; no “soft_wake forced” warnings.
    • stt_ready {"device":"cuda", ... "compute_type":"int8_float16"} in logs.
    • ollama list (host or container) shows your default chat model tag.
    • 10-turn smoke test: median round-trip ≤ 2.5 s for ~1 s utterances.

Phase G — Streaming TTS (Offline UX polish)
Precheck: Phase F acceptance met.
Objectives: Start speaking earlier by chunking text while the LLM is still streaming.
Changes (orchestrator behavior):
    • Enable simulated streaming TTS: accumulate tokens, cut chunks at punctuation or soft length (≈160 chars), add linger_ms to avoid mid-word cuts, crossfade_ms to blend, max_queue to prevent backlog.
    • Optional earcon if first chunk not ready by 450 ms.
Config additions (config/settings.yaml):
tts:
  streaming: true
  chunk_chars: 160
  linger_ms: 150
  crossfade_ms: 60
  max_queue: 3
  earcon_if_ttfa_ms: 450
Acceptance (record timings):
    • TTFA ≤ 600 ms median on short replies.
    • No audible word cuts across 20 turns (≤1% artifacts).
    • TTS queue length never exceeds max_queue in a 60 s read-aloud.

Phase H — Smarter Models & Intent Routing (Offline)
Precheck: Phase G acceptance met.
Objectives: Improve reasoning and coding without tanking latency.
Changes:
    • Add a stronger general model (e.g., DeepSeek-R1 7B at Q4) and a small coder 7B tag.
    • Route by intent: general chat → general model; “dev mode”/code tasks → coder model.
    • Keep your 3B tag as a fallback for lightweight prompts.
Acceptance:
    • Logs show correct model selection per intent.
    • Latency budgets still meet Phase F (≤2.5 s median for short turns).
    • One coding task solved offline using coder model.

Phase I — Connected Mode (Guard-railed)
Precheck: H acceptance met; offline path stable.
Objectives: Add one-switch online tools with snapshots, allowlists, and a visible audit trail.
Definitions:
    • Offline Mode (default): No outbound calls. Online tools are disabled; assistant will say “Online tools disabled.”
    • Connected Mode: Explicitly enabled; online tools available; every call logged and snapshotted.
Master toggle:
    • Environment variable: VELANOVA_EGRESS=1
    • Orchestrator announces mode at boot and on first online tool use.
Config (config/settings.yaml):
security:
  egress_block_expected: true         # default stance for audits
connected:
  enabled: false                      # flips true when VELANOVA_EGRESS=1
  tools:
    web_snapshot: {enabled: true, depth: 1, timeout_s: 15}
    web_search:   {enabled: true, max_results: 3, timeout_s: 15}
    weather:      {enabled: true}
    calendar:     {enabled: false}    # off until configured
  allow_domains:
    - en.wikipedia.org
    - developer.mozilla.org
    - docs.python.org
  store_path: "/mnt/sata_backups/VelaNova/research"
  max_bytes: 5242880
logging:
  citations: true
Secrets (separate file, disabled by default):
    • Path: config/secrets.yml (not required for offline; not read unless a tool asks for it).
    • Default state: absent or empty; all tools that would need a key are disabled until you enable them.
    • Examples (commented placeholders only):
# calendar:
#   caldav_url: ""
#   username: ""
#   password: ""
# weather:
#   api_key: ""
Tool behavior (when Connected Mode ON):
    • Web Snapshot: Depth-1 fetch → store HTML/PDF + citation.txt under /mnt/sata_backups/VelaNova/research/YYYYMMDD/<slug>/.
    • Web Search: Focused search → open top 1–3 results → summarize with inline citations → snapshot sources.
    • Weather/Time: Basic fact fetch; times out fast; cites source.
    • Calendar (optional): Read-only unless you confirm a write. Requires secrets.yml keys and explicit connected.tools.calendar.enabled: true.
Guardrails:
    • Domain allowlist enforced.
    • Robots.txt respected.
    • No cookies/logins unless you later populate secrets.yml and enable that specific tool.
    • Rate/size limits: 3 pages per query; 15 s timeout; 5 MB cap per artifact (override requires your consent).
    • Audit trail: every online call logs URL, timestamp, bytes, local snapshot path; assistant announces “ONLINE (egress on)”.
Acceptance (capture paths and logs):
    • With VELANOVA_EGRESS=1:
        ◦ web snapshot "<url>" stores HTML/PDF and citation in dated folder.
        ◦ weather <city> returns data and cites source.
    • With toggle removed: same requests yield “Online tools disabled” and no network attempt.

Phase J — Ops, Health, and Snapshots
Precheck: I acceptance met.
Objectives: Make it boring to operate and easy to trust.
Changes:
    • Self-test script: tools/selftest.sh does a synthetic one-turn run (wake → canned wav → short reply → TTS wav), exits 0 on success, writes artifacts to logs/.
    • Log rotation: Keep 7 days; ensure file size caps are sane.
    • Snapshots: After each accepted phase, tar configs/models metadata/orchestrator to HDD and append to docs/SNAPSHOTS.md with SHA-256.
Acceptance:
    • selftest.sh exits 0 and produces a short TTS file.
    • Latest snapshot + checksum recorded and verified.
    • OPERATIONS doc has “Start/Stop,” “Mode toggle,” “Self-test,” and “Troubleshooting” sections updated.

3) Phase Kickoff Template (repeat this before any work)
Precheck:
    • Run tools/audit.sh; open the latest AUDIT-*.md.
    • Confirm the previous phase’s acceptance points.
    • If anything is red/unknown, fix that first.
Execution rule:
    • Ask me for the next step. I’ll give you one command and what evidence to paste back. We proceed only after the evidence matches.

4) What “Complete v1” means (final sign-off)
    • Offline: Wake → STT (CUDA) → LLM (local) → Streaming TTS; median round-trip ≤ 2.5 s for short utterances; memory recalls across restarts.
    • Models: General 7B (reasoning) + coder 7B (intent-routed) + a light fallback model; all local and persistent.
    • Streaming TTS: TTFA ≤ 600 ms median; artifacts ≤1%.
    • Connected Mode: One toggle enables tools; every online action is allowlisted, cited, snapshotted to HDD, and logged; toggle off blocks immediately.
    • Ops: Self-test green; snapshots current; OPERATIONS updated.
