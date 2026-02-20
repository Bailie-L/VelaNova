#!/usr/bin/env python3
"""
VelaNova Phase C — Mic Probe
- Records 5s @16k mono -> logs/mic_probe.wav
- Prints RMS/peak and transcribes (CPU int8)
"""
import json
import numpy as np
import sounddevice as sd
import soundfile as sf
from pathlib import Path
from faster_whisper import WhisperModel

BASE = Path.home() / "Projects/VelaNova"
SAMPLE_RATE = 16000
SECONDS = 5
WAV = BASE / "logs/mic_probe.wav"
WAV.parent.mkdir(parents=True, exist_ok=True)

# Record
data = sd.rec(int(SECONDS * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
sd.wait()
sf.write(WAV, data, SAMPLE_RATE)

# Stats
rms  = float(np.sqrt(np.mean(np.square(data), dtype=np.float64)))
peak = float(np.max(np.abs(data)))

# STT
wm = WhisperModel(str(BASE/"models/whisper/small"), device="cpu", compute_type="int8")
segments, _ = wm.transcribe(str(WAV), language="en", vad_filter=False, beam_size=1)
text = "".join(s.text for s in segments).strip()

print(json.dumps({
  "recording": {"path": str(WAV), "seconds": SECONDS, "rms": round(rms, 6), "peak": round(peak, 6)},
  "stt": {"text": text}
}, indent=2))
