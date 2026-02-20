#!/usr/bin/env python3
"""
VelaNova Phase C — Environment Checker
Reports: deps, binaries, model files, config, and local LLM reachability.
"""
from __future__ import annotations
import sys
import json
import shutil
from pathlib import Path

def try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False

BASE = Path("~/Projects/VelaNova").expanduser()
CFG  = BASE / "config/voice.yaml"

report = {
    "python": {"version": sys.version.split()[0], "executable": sys.executable},
    "paths": {},
    "config": {},
    "deps": {},
    "binaries": {},
    "models": {"whisper_dir_ok": None, "piper_voice_ok": None},
    "llm": {"endpoint_ok": None, "model": None},
    "audio": {"input_default": None, "output_default": None},
}

# --- paths
report["paths"]["base_exists"] = BASE.exists()
report["paths"]["cfg_exists"] = CFG.exists()
report["paths"]["models"] = {
    "oww": str(BASE/"models/oww"),
    "whisper": str(BASE/"models/whisper"),
    "piper": str(BASE/"models/piper"),
}

# --- config
if CFG.exists():
    try:
        import yaml  # type: ignore
        with open(CFG, "r") as f:
            cfg = yaml.safe_load(f) or {}
        report["config"]["wake_phrases"] = [p for p in cfg.get("wake", {}).get("phrases", [])]
        report["config"]["stop_phrase"] = cfg.get("wake", {}).get("stop_phrase")
        report["config"]["stt_model"] = cfg.get("stt", {}).get("model")
        report["config"]["tts_voice"] = cfg.get("tts", {}).get("voice")
        report["config"]["tts_voice_path"] = cfg.get("tts", {}).get("voice_path")
        report["config"]["llm_model"] = cfg.get("llm", {}).get("model")
        report["config"]["llm_endpoint"] = cfg.get("llm", {}).get("endpoint")
    except Exception as e:
        report["config"]["error"] = f"{e.__class__.__name__}: {e}"
        cfg = {}
else:
    cfg = {}

# --- deps
deps = {
    "pyyaml": "yaml",
    "sounddevice": "sounddevice",
    "soundfile": "soundfile",
    "numpy": "numpy",
    "webrtcvad": "webrtcvad",              # optional
    "openwakeword": "openwakeword",        # optional
    "faster-whisper": "faster_whisper",
    "requests": "requests",
}
for pkg, mod in deps.items():
    report["deps"][pkg] = try_import(mod)

# --- binaries
report["binaries"]["piper"] = shutil.which("piper")

# --- models on disk
whisper_root = Path(cfg.get("models", {}).get("whisper", str(BASE/"models/whisper")))
stt_model = cfg.get("stt", {}).get("model", "small")
report["models"]["whisper_dir_ok"] = (whisper_root / stt_model).exists()

piper_voice = Path(cfg.get("tts", {}).get("voice_path", str(BASE/"models/piper/voice.onnx")))
report["models"]["piper_voice_ok"] = piper_voice.exists()

# --- LLM (Ollama)
try:
    import requests  # type: ignore
    endpoint = cfg.get("llm", {}).get("endpoint", "http://localhost:11434")
    r = requests.get(endpoint.rstrip("/") + "/api/tags", timeout=2)
    report["llm"]["endpoint_ok"] = (r.status_code == 200)
    report["llm"]["model"] = cfg.get("llm", {}).get("model")
except Exception:
    report["llm"]["endpoint_ok"] = False

# --- audio devices (best-effort)
if report["deps"]["sounddevice"]:
    try:
        import sounddevice as sd  # type: ignore
        def_dev = sd.default.device
        inputs = sd.query_devices(def_dev[0]) if def_dev and def_dev[0] is not None else None
        outputs = sd.query_devices(def_dev[1]) if def_dev and def_dev[1] is not None else None
        report["audio"]["input_default"]  = inputs["name"] if inputs else None
        report["audio"]["output_default"] = outputs["name"] if outputs else None
    except Exception as e:
        report["audio"]["error"] = f"{e.__class__.__name__}: {e}"

print(json.dumps(report, indent=2))
