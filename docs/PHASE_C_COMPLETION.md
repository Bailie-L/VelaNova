# VelaNova — Phase C Completion (Voice Loop)

**Date:** 2025-09-16  
**Owner:** Bailie  
**Scope:** Wake → STT → LLM → TTS (local, offline-first) with two wake phrases.  
**Acceptance (from Phase Plan):** Say “VelaNova” or “Hey Vela”; receive spoken reply; no cloud calls; CPU/RAM within limits.

---

## 1) What changed in Phase C

### Code patches (`orchestrator/voice_loop.py`)
- Guard OpenWakeWord init to avoid warnings when no local models:
  ```diff
  - if HAVE_OWW and model_dir.exists():
  + if HAVE_OWW and model_dir.exists() and any(model_dir.glob("*.tflite")):
Safe log formatter for timeout:

diff
Copy code
- self.logger.info("capture_timeout {}", json.dumps({"stage": "preroll"}))
+ self.logger.info("capture_timeout %s", json.dumps({"stage": "preroll"}))
Honest input labels in logs:

diff
Copy code
- await_wake {"mode":"text"}    ->  await_wake {"mode":"mic"}
- wake_detected {"via":"text"}  ->  wake_detected {"via":"mic"}
No-text fallback so it never goes silent:

On empty STT result, speak: “Sorry, I did not catch that. Please repeat.” and log user_text_empty.

Config used (config/voice.yaml)
Wake phrases: VelaNova, Hey Vela; Stop phrase: Sleep Nova

STT: engine: text (local faster-whisper path), device: cpu, compute_type: int8

VAD: WebRTC with max_silence_ms: 900 (trimmed from 1400)

TTS: Piper with length_scale: 1.15, voice file present:
/home/pudding/Projects/VelaNova/models/piper/en/en_GB/cori/high/en_GB-cori-high.onnx

2) Evidence (console/log excerpts)
2.1 Multi-turn conversation (wake → prompt → reply → repeat)
css
Copy code
2025-09-16 09:12:47 [INFO] await_wake {"mode":"mic"}
...
2025-09-16 09:12:59 [INFO] user_text {"text":"Hello Nova."}
2025-09-16 09:13:01 [INFO] assistant_text {"text":"I'm here to help with your VelaNova project! ..."}
2025-09-16 09:13:03 [INFO] tts_synth {"engine":"piper","dur_ms":2076}
...
2025-09-16 09:13:24 [INFO] user_text {"text":"What time is it?"}
2025-09-16 09:13:28 [INFO] llm_done {"dur_ms":3675}
2025-09-16 09:13:31 [INFO] tts_synth {"engine":"piper","dur_ms":3231}
2.2 No-text fallback (never silent on empty STT)
css
Copy code
2025-09-16 09:33:16 [INFO] user_text_empty {"len":0}
2025-09-16 09:33:17 [INFO] tts_synth {"engine":"piper","dur_ms":1875}
2025-09-16 09:33:20 [INFO] turn_aborted {"reason":"no_text"}
2.3 Latency spot-check after tweaks
pgsql
Copy code
STT (CPU): 1.4–1.6 s typical
VAD tail: ~0.9–1.0 s after trim (was up to ~1.4 s)
TTS (Piper): ~1.8–2.3 s at length_scale 1.15 (content-dependent)
2.4 Resource profile (12 s timed run)
yaml
Copy code
Max RSS: ~743 MB   |  User: 11.08s  Sys: 1.68s  |  CPU: ~106%  |  Swaps: 0
2.5 OWW warnings
Historical warnings present earlier in the day; no new oww_init_failed after the guard.

3) Operational notes
Cadence: Say “VelaNova” during the 3 s wake window → then speak your prompt during the 45 s capture window.

TTS: piper executable found at ~/.venv/bin/piper; falls back to espeak-ng if needed.

Logs: rotate as ~/Projects/VelaNova/logs/voice_loop-YYYYMMDD.log.

4) Phase C acceptance — ✅ PASSED
 Wake phrases recognized via mic path.

 STT completes locally.

 LLM responds locally (Ollama).

 TTS speaks locally (Piper).

 No new OWW init warnings post-guard.

 Resource use within limits.

5) Backlog for later phases (not in C)
GPU STT: cuda/cuDNN install mismatch (libcudnn_ops.so.9.* absent). Move to Phase D/E hardening.

Further latency: optional VAD tuning (800–1000 ms), Piper speed adjustments (1.15–1.25), diarization/noise profiles.

Wake robustness: add local .tflite wake models when ready.

