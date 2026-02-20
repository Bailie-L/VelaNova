#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VelaNova — Enhanced Orchestrator Voice Loop v3
Phases A–H consolidated with production improvements

Enhanced Features:
- Real SQLite FTS5 memory with conversation history
- Actual audio capture with PyAudio/sounddevice
- OpenWakeWord integration for wake detection
- Embedding-based semantic search (sentence-transformers)
import re
- Multi-turn conversation state tracking
- Enhanced local intent handling
- Conversation context window management
- Audio level monitoring and VAD
- Interrupt handling during TTS playback
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Deque

import numpy as np

# Core deps with graceful fallbacks
try:
    import requests
except ImportError:
    requests = None

try:
    import yaml
except ImportError:
    yaml = None
    print("WARNING: PyYAML required for config loading")
    sys.exit(1)

# Audio deps
try:
    import sounddevice as sd
    AUDIO_BACKEND = "sounddevice"
except ImportError:
    sd = None
    try:
        import pyaudio
        AUDIO_BACKEND = "pyaudio"
    except ImportError:
        pyaudio = None
        AUDIO_BACKEND = None

# STT deps
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None
    WHISPER_AVAILABLE = False

# Wake word deps
try:
    import openwakeword
    from openwakeword.model import Model as OWWModel
    OWW_AVAILABLE = True
except ImportError:
    openwakeword = None
    OWWModel = None
    OWW_AVAILABLE = False

# Embedding deps for semantic search
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    SentenceTransformer = None
    EMBEDDINGS_AVAILABLE = False

# Keyboard interrupt support (Phase H P1.1)
try:
    from pynput import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    keyboard = None
    KEYBOARD_AVAILABLE = False

# =========================
# Config & Logging
# =========================

CONFIG_PATH = Path("~/Projects/VelaNova/config/voice.yaml").expanduser()
LOG_DIR = Path("~/Projects/VelaNova/logs").expanduser()
DATA_DIR = Path("~/Projects/VelaNova/data").expanduser()
MEMORY_DB = DATA_DIR / "memory.db"
MODELS_DIR = Path("~/Projects/VelaNova/models").expanduser()

LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    """Load YAML config from the canonical voice.yaml path."""
    if not CONFIG_PATH.exists():
        # Create default config if missing
        default_cfg = {
            "version": "v3",
            "wake": {
                "mode": "text",
                "phrases": ["hey mycroft", "hey jarvis", "alexa"],
                "stop_phrase": "sleep nova",
                "trigger_debounce_ms": 1500,
                "sensitivity": 0.5,
                "model_path": str(MODELS_DIR / "wake")
            },
            "stt": {
                "model": "small",
                "device": "cuda",
                "compute_type": "int8_float16",
                "beam_size": 1,
                "language": "en"
            },
            "tts": {
                "engine": "piper",
                "piper_bin": "/usr/bin/piper",
                "piper_voice": None,
                "player_bin": "aplay",
                "streaming": True,
                "chunk_chars": 160,
                "grace_after_ms": 450
            },
            "llm": {
                "model": "llama3.2:3b",
                "host": "http://127.0.0.1:11434",
                "timeout_s": 15.0,
                "max_context_turns": 5
            },
            "orchestrator": {
                "mode": "text",
                "vad_threshold": 0.02,
                "silence_duration": 1.5
            },
            "memory": {
                "enabled": True,
                "max_history": 100,
                "embedding_model": "all-MiniLM-L6-v2"
            },
            "dev": {
                "enabled": False,
                "coder_model": "llama3.2-coder:local"
            },
            "connected": {"enabled": False},
            "security": {"egress_block_expected": True}
        }
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(default_cfg, f)
        cfg = default_cfg
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    # Normalize sections
    for key in ["wake", "tts", "llm", "stt", "orchestrator", "memory", "dev", "connected", "security"]:
        cfg.setdefault(key, {})

    cfg.setdefault("version", "v3")

    # Ensure model path is set
    if "model_path" not in cfg["wake"]:
        cfg["wake"]["model_path"] = str(MODELS_DIR / "wake")

    return cfg


def ensure_logger(log_cfg: Dict[str, Any]) -> Tuple[logging.Logger, str]:
    """Set up file + stdout logger."""
    ts = datetime.now().strftime("%Y%m%d")
    log_path = LOG_DIR / f"voice_loop-{ts}.log"

    logger = logging.getLogger("velanova.voice")
    logger.setLevel(logging.DEBUG if log_cfg.get("debug") else logging.INFO)
    logger.handlers.clear()

    # File handler
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)

    # Format
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger, str(log_path)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_event(logger: logging.Logger, kind: str, payload: Dict[str, Any]):
    try:
        logger.info("%s %s", kind, json.dumps(payload))
    except Exception:
        logger.info("%s %s", kind, str(payload))


# =========================
# Memory System with SQLite FTS5
# =========================

class MemoryStore:
    """SQLite FTS5-based memory with embeddings support."""

    def __init__(self, db_path: Path, logger: logging.Logger, cfg: Dict[str, Any]):
        self.db_path = db_path
        self.logger = logger
        self.cfg = cfg.get("memory", {})
        self.enabled = self.cfg.get("enabled", True)
        self.max_history = self.cfg.get("max_history", 100)
        self.semantic_threshold = self.cfg.get("semantic_threshold", 0.65)
        self.semantic_search_limit = self.cfg.get("semantic_search_limit", 5)

        # Embedding model for semantic search
        self.embedder = None
        if EMBEDDINGS_AVAILABLE and self.enabled:
            model_name = self.cfg.get("embedding_model", "all-MiniLM-L6-v2")
            try:
                self.embedder = SentenceTransformer(model_name)
                self.logger.info("embeddings_ready %s", json.dumps({"model": model_name, "semantic_threshold": self.semantic_threshold}))
            except Exception as e:
                self.logger.warning("embeddings_failed %s", json.dumps({"error": str(e)}))

        if self.enabled:
            self._init_db()

    def _init_db(self):
        """Initialize SQLite with FTS5."""
        try:
            conn = sqlite3.connect(self.db_path)

            # Check database integrity first
            try:
                cursor = conn.execute("PRAGMA integrity_check")
                result = cursor.fetchone()
                if result and result[0] != "ok":
                    self.logger.warning("db_integrity_issue %s", json.dumps({"result": result[0]}))
                    # Backup corrupted DB
                    if self.db_path.exists():
                        backup_path = self.db_path.with_suffix('.db.backup')
                        shutil.copy2(self.db_path, backup_path)
                        self.logger.info("db_backed_up %s", json.dumps({"path": str(backup_path)}))
                        # Remove corrupted database and reinitialize
                        conn.close()
                        self.db_path.unlink(missing_ok=True)
                        self.logger.warning("db_reinitializing %s", json.dumps({"reason": "corruption"}))
                        conn = sqlite3.connect(self.db_path)
                        conn.execute("PRAGMA journal_mode=WAL")
            except Exception as e:
                self.logger.warning("db_integrity_check_failed %s", json.dumps({"error": str(e)}))

            conn.execute("PRAGMA journal_mode=WAL")

            # Conversations table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_num INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    embedding BLOB,
                    metadata TEXT
                )
            """)

            # FTS5 for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts
                USING fts5(content, role, session_id, content=conversations, content_rowid=id)
            """)

            # Triggers to keep FTS in sync
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS conversations_ai AFTER INSERT ON conversations BEGIN
                    INSERT INTO conversations_fts(rowid, content, role, session_id)
                    VALUES (new.id, new.content, new.role, new.session_id);
                END
            """)

            conn.commit()
            conn.close()
            self.logger.info("memory_initialized %s", json.dumps({"db": str(self.db_path)}))
        except Exception as e:
            self.logger.error("memory_init_failed %s", json.dumps({"error": str(e)}))
            self.enabled = False

    def add_turn(self, session_id: str, turn_num: int, role: str, content: str, metadata: Optional[Dict] = None):
        """Add a conversation turn."""
        if not self.enabled:
            return

        try:
            conn = sqlite3.connect(self.db_path)

            # Generate embedding if available
            embedding = None
            if self.embedder:
                try:
                    embedding = self.embedder.encode(content).tobytes()
                except Exception as e:
                    self.logger.warning("embedding_failed %s", json.dumps({"error": str(e)}))

            conn.execute("""
                INSERT INTO conversations (session_id, turn_num, role, content, embedding, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, turn_num, role, content, embedding, json.dumps(metadata or {})))

            conn.commit()
            conn.close()

            self.logger.debug("memory_turn_added %s", json.dumps({
                "session": session_id, "turn": turn_num, "role": role, "chars": len(content)
            }))
        except Exception as e:
            self.logger.error("memory_add_failed %s", json.dumps({"error": str(e)}))

    def get_recent_turns(self, session_id: str, limit: int = 5) -> List[Tuple[str, str]]:
        """Get recent conversation turns."""
        if not self.enabled:
            return []

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT role, content FROM conversations
                WHERE session_id = ?
                ORDER BY turn_num DESC
                LIMIT ?
            """, (session_id, limit))

            turns = [(row[0], row[1]) for row in cursor.fetchall()]
            conn.close()

            return list(reversed(turns))
        except Exception as e:
            self.logger.error("memory_get_failed %s", json.dumps({"error": str(e)}))
            return []

    def search_semantic(self, query: str, limit: int = 3) -> List[Tuple[str, float]]:
        """Semantic search using embeddings with threshold filtering."""
        if not self.enabled or not self.embedder:
            return []

        try:
            query_emb = self.embedder.encode(query)

            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT content, embedding, timestamp FROM conversations
                WHERE embedding IS NOT NULL
                ORDER BY id DESC
                LIMIT 100
            """)

            results = []
            excluded = []

            row_count = 0
            for content, emb_bytes, ts in cursor:
                row_count += 1
                self.logger.debug("semantic_search_row %s", json.dumps({"row": row_count, "has_emb": emb_bytes is not None, "emb_len": len(emb_bytes) if emb_bytes else 0, "content_preview": content[:50]}))
                if emb_bytes:
                    try:
                        emb = np.frombuffer(emb_bytes, dtype=np.float32)
                        similarity = np.dot(query_emb, emb) / (np.linalg.norm(query_emb) * np.linalg.norm(emb))
                        # Apply recency boost (newer = higher)
                        # Calculate recency boost: newer memories get higher weight
                        from datetime import datetime
                        age_hours = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600
                        recency_boost = max(0.0, 1.0 - (age_hours / 720))  # Decay over 30 days
                        score = float(similarity * (0.7 + 0.3 * recency_boost))

                        # Filter out query echoes (>0.95 similarity)
                        if score > 0.95:
                            self.logger.debug("semantic_echo_filtered %s", json.dumps({"score": round(score, 4), "content": content[:60]}))
                            continue
                        if score >= self.semantic_threshold:
                            results.append((content, score))
                        elif score >= self.semantic_threshold - 0.05:
                            # Track near-misses (within 0.05 of threshold)
                            excluded.append((content[:60], score))
                    except Exception as e:
                        self.logger.error("semantic_search_row_failed %s", json.dumps({"row": row_count, "error": str(e), "type": type(e).__name__, "content": content[:50]}))
                        continue

            self.logger.debug("semantic_search_complete %s", json.dumps({"rows_fetched": row_count, "results_count": len(results), "excluded_count": len(excluded)}))
            conn.close()

            # Sort by score and return top matches
            results.sort(key=lambda x: x[1], reverse=True)
            top_results = results[:limit]

            # Log summary
            self.logger.info("semantic_search %s", json.dumps({
                "query_chars": len(query),
                "total_scored": len(results) + len(excluded),
                "above_threshold": len(results),
                "returned": len(top_results),
                "excluded_near_miss": len(excluded),
                "threshold": self.semantic_threshold
            }))

            # Log top results with scores
            for i, (content, score) in enumerate(top_results):
                self.logger.debug("semantic_hit %s", json.dumps({
                    "rank": i + 1,
                    "score": round(score, 4),
                    "content": content[:80]
                }))

            # Log excluded near-misses
            for content_preview, score in excluded[:3]:  # Limit to top 3 near-misses
                self.logger.debug("semantic_excluded %s", json.dumps({
                    "score": round(score, 4),
                    "threshold": self.semantic_threshold,
                    "delta": round(self.semantic_threshold - score, 4),
                    "content": content_preview
                }))

            return top_results
        except Exception as e:
            self.logger.error("semantic_search_failed %s", json.dumps({"error": str(e)}))
            return []

    def search_fts(self, query: str, limit: int = 5) -> List[str]:
        """Full-text search."""
        if not self.enabled:
            return []

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT content FROM conversations_fts
                WHERE conversations_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (query, limit))

            results = [row[0] for row in cursor.fetchall()]
            conn.close()

            return results
        except Exception as e:
            self.logger.error("fts_search_failed %s", json.dumps({"error": str(e)}))
            return []


# =========================
# Audio Capture with VAD
# =========================

    def get_latest_session(self, max_age_hours: int = 24) -> Optional[str]:
        """Get most recent session if within age limit."""
        if not self.enabled:
            return None

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT session_id, MAX(timestamp) as last_activity
                FROM conversations
                GROUP BY session_id
                ORDER BY last_activity DESC
                LIMIT 1
            """)

            row = cursor.fetchone()
            conn.close()

            if row:
                session_id, last_activity = row
                last_time = datetime.fromisoformat(last_activity)
                age_hours = (datetime.now() - last_time).total_seconds() / 3600

                if age_hours <= max_age_hours:
                    self.logger.info("session_candidate %s", json.dumps({
                        "session_id": session_id,
                        "age_hours": round(age_hours, 2)
                    }))
                    return session_id

            return None
        except Exception as e:
            self.logger.error("get_latest_session_failed %s", json.dumps({"error": str(e)}))
            return None


    def get_session_info(self, session_id: str) -> Dict[str, Any]:
        """Get session metadata."""
        if not self.enabled:
            return {}

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as turn_count,
                    MIN(timestamp) as started,
                    MAX(timestamp) as last_activity
                FROM conversations
                WHERE session_id = ?
            """, (session_id,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    "turn_count": row[0],
                    "started": row[1],
                    "last_activity": row[2]
                }
            return {}
        except Exception as e:
            self.logger.error("get_session_info_failed %s", json.dumps({"error": str(e)}))
            return {}
class AudioCapture:
    """Real audio capture with voice activity detection."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        self.cfg = cfg.get("orchestrator", {})
        self.logger = logger
        self.sample_rate = 16000
        self.channels = 1
        self.chunk_size = 512
        self.vad_threshold = self.cfg.get("vad_threshold", 0.02)
        self.silence_duration = self.cfg.get("silence_duration", 1.5)

        self.backend = AUDIO_BACKEND
        self.logger.info("audio_backend %s", json.dumps({"backend": self.backend or "none"}))

    def capture_until_silence(self, timeout: float = 10.0) -> Optional[np.ndarray]:
        """Capture audio until silence detected."""
        if not self.backend:
            return None

        frames = []
        silence_chunks = 0
        chunks_for_silence = int(self.silence_duration * self.sample_rate / self.chunk_size)
        start_time = time.time()

        self.logger.info("capture_begin %s", json.dumps({
            "timeout_s": timeout, "vad": "rms", "threshold": self.vad_threshold
        }))

        try:
            if self.backend == "sounddevice":
                with sd.InputStream(samplerate=self.sample_rate, channels=self.channels,
                                   dtype='int16', blocksize=self.chunk_size) as stream:
                    while time.time() - start_time < timeout:
                        data, _ = stream.read(self.chunk_size)
                        frames.append(data)

                        # Simple RMS-based VAD
                        rms = np.sqrt(np.mean(data.astype(np.float32) ** 2)) / 32768.0

                        if rms < self.vad_threshold:
                            silence_chunks += 1
                            if silence_chunks >= chunks_for_silence:
                                break
                        else:
                            silence_chunks = 0

            elif self.backend == "pyaudio":
                p = pyaudio.PyAudio()
                stream = p.open(format=pyaudio.paInt16, channels=self.channels,
                              rate=self.sample_rate, input=True,
                              frames_per_buffer=self.chunk_size)

                while time.time() - start_time < timeout:
                    data = stream.read(self.chunk_size, exception_on_overflow=False)
                    audio_data = np.frombuffer(data, dtype=np.int16)
                    frames.append(audio_data)

                    rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2)) / 32768.0

                    if rms < self.vad_threshold:
                        silence_chunks += 1
                        if silence_chunks >= chunks_for_silence:
                            break
                    else:
                        silence_chunks = 0

                stream.stop_stream()
                stream.close()
                p.terminate()
        except Exception as e:
            self.logger.error("capture_failed %s", json.dumps({"error": str(e)}))
            return None

        duration = time.time() - start_time
        self.logger.info("capture_end %s", json.dumps({
            "sec": round(duration, 2), "blocks": len(frames)
        }))

        if frames:
            return np.concatenate(frames)
        return None


# =========================
# Wake Detection Enhanced
# =========================

@dataclass
class WakeDetector:
    """Enhanced wake detection with OpenWakeWord support."""

    cfg: Dict[str, Any]
    logger: logging.Logger
    mode: str = "text"
    phrases: List[str] = field(default_factory=list)
    stop_phrase: str = "sleep nova"
    sensitivity: float = 0.5
    oww_model: Optional[Any] = None
    model_path: Optional[Path] = None

    def __post_init__(self):
        wake = self.cfg.get("wake", {})
        self.mode = wake.get("mode", "text")
        self.phrases = [p.lower().replace(" ", "_") for p in wake.get("phrases", ["velanova", "hey nova"])]
        self.stop_phrase = wake.get("stop_phrase", "sleep nova").lower()
        self.sensitivity = wake.get("sensitivity", 0.5)
        self.model_path = Path(wake.get("model_path", MODELS_DIR / "wake"))

        # Initialize OpenWakeWord if available and in mic mode
        if OWW_AVAILABLE and self.mode == "mic":
            try:
                # Check for ONNX models (not TFLite)
                melspec_path = self.model_path / "melspectrogram.onnx"

                if not melspec_path.exists():
                    self.logger.warning("oww_model_not_found %s", json.dumps({
                        "path": str(melspec_path),
                        "advice": "Place ONNX models in models/wake/ directory"
                    }))
                    return

                # Set custom model path and initialize
                os.environ["OPENWAKEWORD_MODEL_DIR"] = str(self.model_path)
                self.oww_model = OWWModel(
                    wakeword_models=["alexa", "hey_mycroft", "hey_jarvis"],
                    inference_framework="onnx"
                )

                # Configure GPU providers for ONNX inference
                if self.oww_model and hasattr(self.oww_model, 'models'):
                    for model_name, session in self.oww_model.models.items():
                        try:
                            session.set_providers(['CUDAExecutionProvider', 'CPUExecutionProvider'])
                            actual_providers = session.get_providers()
                            self.logger.info("oww_gpu_config %s", json.dumps({
                                "model": model_name,
                                "providers": actual_providers,
                                "gpu_enabled": 'CUDAExecutionProvider' in actual_providers
                            }))
                        except Exception as e:
                            self.logger.warning("oww_gpu_config_failed %s", json.dumps({
                                "model": model_name,
                                "error": str(e)
                            }))


                # Validate models loaded
                if not self.oww_model.models:
                    raise RuntimeError("OWW initialized but no models loaded")

                model_names = list(self.oww_model.models.keys())
                self.logger.info("oww_initialized %s", json.dumps({
                    "model_path": str(self.model_path),
                    "inference_framework": "onnx",
                    "models_loaded": len(model_names),
                    "model_names": model_names
                    }))
            except Exception as e:
                self.logger.warning("oww_init_failed %s", json.dumps({"error": str(e)}))


    def detect_in_audio_stream(self, audio: np.ndarray, threshold_override: Optional[float] = None) -> bool:
        """Detect wake word in audio buffer using proper streaming chunks."""
        if not self.oww_model or audio is None or len(audio) == 0:
            return False

        try:
            # Flatten audio if needed (sounddevice returns [N,1] even with channels=1)
            if audio.ndim > 1:
                audio = audio.flatten()
            # Normalize int16 to float32 range [-1.0, 1.0] for OWW
            if audio.dtype == np.int16:
                audio = audio.astype(np.float32) / 32768.0
            audio = audio - np.mean(audio)  # Remove DC offset for OWW

            # Diagnostic: log processed audio characteristics
            self.logger.info("oww_audio_input %s", json.dumps({
                "shape": audio.shape,
                "ndim": audio.ndim,
                "dtype": str(audio.dtype),
                "length": len(audio),
                "min": float(np.min(audio)),
                "max": float(np.max(audio)),
                "mean": float(np.mean(audio))
            }))

            frame_size = 1280
            hop_size = 640  # 50% overlap for better detection

            # Process audio in sliding windows
            for start in range(0, len(audio) - frame_size + 1, hop_size):
                frame = audio[start:start + frame_size]
                frame = frame - np.mean(frame)  # Remove DC offset per frame

                # Get prediction from OWW

                prediction = self.oww_model.predict(frame)

                frame_rms = float(np.sqrt(np.mean(frame**2)))
                # Verify frame is changing
                frame_hash = hash(frame.tobytes())
                self.logger.info("oww_frame_hash %s", json.dumps({"offset": start, "hash": frame_hash}))

                self.logger.info("oww_frame_debug %s", json.dumps({
                    "frame_offset": start,
                    "rms": round(frame_rms, 4),
                    "min": round(float(frame.min()), 4),
                    "max": round(float(frame.max()), 4),
                    "mean": round(float(frame.mean()), 4)
                }))

                scores = {}
                for word in self.phrases:
                    if word in prediction:
                        scores[word] = float(prediction[word])

                if any(scores.values()):  # Only log if non-zero scores
                    self.logger.info("oww_prediction %s", json.dumps({
                        "frame_offset": start,
                        "scores": scores
                    }))

                # Check if any recent frame in buffer exceeded threshold
                for word in self.phrases:
                    if word in self.oww_model.prediction_buffer:
                        buffer_max = max(self.oww_model.prediction_buffer[word])
                        buffer_contents = list(self.oww_model.prediction_buffer[word])
                        self.logger.info("buffer_inspect %s", json.dumps({"word": word, "buffer_len": len(buffer_contents), "buffer_max": float(buffer_max), "last_5": [float(x) for x in buffer_contents[-5:]]}))

                        threshold = threshold_override if threshold_override is not None else self.sensitivity
                        if buffer_max >= threshold:
                            self.logger.info("wake_detected %s", json.dumps({
                                "word": word,
                                "score": float(buffer_max),
                                "threshold": self.sensitivity
                            }))
                            # Clear prediction buffer to prevent false positives from old scores
                            for model_name in self.oww_model.prediction_buffer:
                                self.oww_model.prediction_buffer[model_name].clear()
                            return True

            return False

        except Exception as e:
            self.logger.error("oww_detection_error %s", json.dumps({"error": str(e)}))
            return False

class STT:
    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        self._whisper = None

        stt_cfg = cfg.get("stt", {})
        self.model_tag = stt_cfg.get("model", "small")
        self.device = stt_cfg.get("device", "cuda")
        self.compute_type = stt_cfg.get("compute_type", "int8_float16")
        self.beam_size = stt_cfg.get("beam_size", 1)
        self.language = stt_cfg.get("language", "en")

        self.initial_prompt = stt_cfg.get("initial_prompt", None)
        # Initialize Whisper if available
        if WHISPER_AVAILABLE and self.device == "cuda":
            try:
                import torch
                if torch.cuda.is_available():
                    self._whisper = WhisperModel(
                        self.model_tag,
                        device="cuda",
                        compute_type=self.compute_type
                    )
                    self.logger.info("stt_ready %s", json.dumps({
                        "engine": "whisper-cuda",
                        "model": self.model_tag,
                        "compute_type": self.compute_type
                    }))
                else:
                    self._init_cpu_whisper()
            except Exception as e:
                self.logger.warning("stt_cuda_failed %s", json.dumps({"error": str(e)}))
                self._init_cpu_whisper()
        else:
            self._init_cpu_whisper()

    def _init_cpu_whisper(self):
        """Fallback to CPU Whisper."""
        if WHISPER_AVAILABLE:
            try:
                self._whisper = WhisperModel(self.model_tag, device="cpu", compute_type="int8")
                self.logger.info("stt_ready %s", json.dumps({
                    "engine": "whisper-cpu", "model": self.model_tag
                }))
            except Exception as e:
                self.logger.warning("stt_cpu_failed %s", json.dumps({"error": str(e)}))

    def transcribe_audio(self, audio: np.ndarray) -> str:
        """Transcribe audio buffer."""
        if audio is None or not self._whisper:
            return ""

        try:
            # Normalize audio to float32 [-1, 1]
            audio_float = audio.astype(np.float32) / 32768.0

            # Transcribe
            segments, info = self._whisper.transcribe(
                audio_float,
                beam_size=self.beam_size,
                language=self.language,
                initial_prompt=self.initial_prompt
            )

            # Collect text
            text = " ".join([seg.text for seg in segments]).strip()

            self.logger.info("stt_done %s", json.dumps({
                "engine": "whisper",
                "len": len(text),
                "lang": info.language if info else self.language
            }))

            return text
        except Exception as e:
            self.logger.error("stt_failed %s", json.dumps({"error": str(e)}))
            return ""


# =========================
# TTS Enhanced with Interrupt Support
# =========================

class TTS:
    """Enhanced TTS with real Piper and interrupt support."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger, interrupt_event=None):
        self.cfg = cfg.get("tts", {})
        self.logger = logger
        self.interrupt_event = interrupt_event  # P1.1 spacebar interrupt

        self.engine = self.cfg.get("engine", "piper")
        self.piper_bin = self.cfg.get("piper_bin", shutil.which("piper"))
        self.piper_voice = self.cfg.get("piper_voice") or self.cfg.get("voice_path")
        self.player_bin = self.cfg.get("player_bin", shutil.which("aplay") or shutil.which("paplay"))

        # Streaming settings
        self.streaming = self.cfg.get("streaming", True)
        self.chunk_chars = self.cfg.get("chunk_chars", 160)
        self.linger_ms = self.cfg.get("linger_ms", 150)
        self.crossfade_ms = self.cfg.get("crossfade_ms", 60)
        self.max_queue = self.cfg.get("max_queue", 3)
        self.earcon_if_ttfa_ms = self.cfg.get("earcon_if_ttfa_ms", 450)

        self.current_process: Optional[subprocess.Popen] = None
        self.last_dur_ms = 0
        self.last_ttfa_ms = 0

        # Phase G queue management
        self.chunk_queue = deque(maxlen=self.max_queue)
        self.queue_lock = threading.Lock()

        # Validate Piper setup
        if self.engine == "piper":
            if not self.piper_bin or not os.path.exists(self.piper_bin):
                self.logger.warning("piper_not_found %s", json.dumps({"bin": self.piper_bin}))
                self.engine = "espeak"
            elif not self.piper_voice or not os.path.exists(self.piper_voice):
                self.logger.warning("piper_voice_not_found %s", json.dumps({"voice": self.piper_voice}))
                self.engine = "espeak"

    def _get_queue_depth(self) -> int:
        """Get current queue depth thread-safely."""
        with self.queue_lock:
            return len(self.chunk_queue)

    def _play_earcon(self, duration_ms: int = 100, frequency: int = 800):
        """Play brief tone to indicate first audio."""
        try:
            # Generate sine wave
            sample_rate = 16000
            samples = int(sample_rate * duration_ms / 1000)
            t = np.linspace(0, duration_ms/1000, samples, False)
            tone = np.sin(2 * np.pi * frequency * t) * 0.3  # 30% volume

            # Convert to int16
            audio = (tone * 32767).astype(np.int16)

            # Play via sounddevice if available
            if sd:
                sd.play(audio, sample_rate)
                sd.wait()
            else:
                # Fallback: espeak beep
                subprocess.run(["beep", "-f", str(frequency), "-l", str(duration_ms)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            self.logger.debug("earcon_played %s", json.dumps({
                "duration_ms": duration_ms, "frequency": frequency
            }))
        except Exception as e:
            self.logger.warning("earcon_failed %s", json.dumps({"error": str(e)}))

    def _strip_markdown(self, text: str) -> str:
        """Remove markdown formatting for TTS."""
        import re
        # Remove bold/italic (**text** or *text*)
        text = re.sub(r'\*\*([^\*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^\*]+)\*', r'\1', text)
        # Remove inline code (`code`)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        # Remove headers (# text)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        # Remove list markers (1. or - or *)
        text = re.sub(r'^\s*[\d\-\*]+\.?\s+', '', text, flags=re.MULTILINE)
        # Remove links [text](url)
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        return text.strip()
    def _chunk_text(self, text: str, max_chars: int) -> List[str]:
        """Chunk text at sentence boundaries for natural pauses."""
        import re

        if len(text) <= max_chars:
            return [text]

        # Split into sentences (periods, !, ?)
        sentences = re.split(r'(?<=[.!?])\s+', text)

        chunks = []
        current_chunk = ""

        for sentence in sentences:
            # If adding this sentence exceeds max, save current and start new
            if current_chunk and len(current_chunk) + len(sentence) + 1 > max_chars:
                chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                current_chunk += (" " if current_chunk else "") + sentence

        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks if chunks else [text]
    def speak(self, text: str, interruptible: bool = True) -> bool:
        """Speak text with parallel synthesis and playback."""
        if not text:
            return True

        # P1.1: Clear any previous interrupt
        if self.interrupt_event:
            self.interrupt_event.clear()

        text = self._strip_markdown(text)
        t0 = time.time()

        if self.streaming and self.engine == "piper":
            text_chunks = self._chunk_text(text, self.chunk_chars)
            chunks = len(text_chunks)

            # Queues for parallel processing
            playback_queue = queue.Queue(maxsize=self.max_queue)
            stop_event = threading.Event()
            errors = []

            # Producer: synthesize chunks
            def synthesizer():
                try:
                    for i in range(chunks):
                        if stop_event.is_set():
                            break
                        chunk_text = text_chunks[i]

                        # Synthesize
                        tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                        tmp_wav.close()

                        t_synth = time.time()
                        proc = subprocess.Popen(
                            [self.piper_bin, "--cuda", "-m", self.piper_voice, "-f", tmp_wav.name],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True
                        )
                        stdout, stderr = proc.communicate(input=chunk_text, timeout=30)
                        synth_ms = int((time.time() - t_synth) * 1000)

                        if proc.returncode == 0:
                            playback_queue.put((i, tmp_wav.name, len(chunk_text), synth_ms))
                            self.logger.info("tts_synth_complete %s", json.dumps({
                                "chunk": i + 1, "chars": len(chunk_text), "synth_ms": synth_ms
                            }))
                        else:
                            errors.append(f"Synthesis failed: {stderr}")
                            stop_event.set()
                            break
                except Exception as e:
                    errors.append(str(e))
                    stop_event.set()
                finally:
                    playback_queue.put(None)  # Sentinel

            # Consumer: play chunks
            def player():
                ttfa_logged = False
                try:
                    while True:
                        item = playback_queue.get()
                        if item is None:  # Sentinel
                            break

                        i, wav_file, chars, synth_ms = item

                        # Log TTFA on first chunk
                        if i == 0 and not ttfa_logged:
                            ttfa_ms = int((time.time() - t0) * 1000)
                            self.logger.info("tts_ttfa_ms %s", json.dumps({"ms": ttfa_ms}))
                            self.last_ttfa_ms = ttfa_ms
                            ttfa_logged = True

                        # Play
                        t_play = time.time()
                        proc = subprocess.Popen(
                            [self.player_bin, wav_file],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        # P1.1: Poll for interrupt instead of blocking wait
                        while proc.poll() is None:
                            if self.interrupt_event and self.interrupt_event.is_set():
                                proc.terminate()
                                proc.wait()
                                self.logger.info("tts_interrupted %s", json.dumps({"chunk": i + 1, "reason": "spacebar"}))
                                stop_event.set()
                                return
                            time.sleep(0.05)
                        play_ms = int((time.time() - t_play) * 1000)

                        # Log profile
                        self.logger.info("tts_profile %s", json.dumps({
                            "chunk": i + 1,
                            "chars": chars,
                            "synth_ms": synth_ms,
                            "play_ms": play_ms,
                            "parallel": True
                        }))

                        # Cleanup
                        try:
                            os.unlink(wav_file)
                        except Exception:
                            pass

                        playback_queue.task_done()

                        # Linger
                        time.sleep(self.linger_ms / 1000.0)

                except Exception as e:
                    errors.append(str(e))
                    stop_event.set()

            # Start threads
            synth_thread = threading.Thread(target=synthesizer, daemon=True)
            play_thread = threading.Thread(target=player, daemon=True)

            synth_thread.start()
            play_thread.start()

            # Wait for completion
            synth_thread.join()
            play_thread.join(timeout=60.0)

            if errors:
                self.logger.error("tts_parallel_failed %s", json.dumps({"errors": errors}))
                return False

        else:
            # Non-streaming fallback
            success = self._synthesize_and_play(text)
            if not success:
                return False

        self.last_dur_ms = int((time.time() - t0) * 1000)
        return True
    def _synthesize_and_play(self, text: str) -> bool:
        """Synthesize and play audio with profiling."""
        t_total = time.time()
        try:
            if self.engine == "piper" and self.piper_bin and self.piper_voice:
                # Use Piper
                tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp_wav.close()

                # Synthesize (PROFILED)
                t_synth = time.time()
                proc = subprocess.Popen(
                    [self.piper_bin, "--cuda", "-m", self.piper_voice, "-f", tmp_wav.name],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                stdout, stderr = proc.communicate(input=text, timeout=30)
                synth_ms = int((time.time() - t_synth) * 1000)

                if proc.returncode == 0 and self.player_bin:
                    # Play (PROFILED)
                    t_play = time.time()
                    self.current_process = subprocess.Popen(
                        [self.player_bin, tmp_wav.name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    self.current_process.wait()
                    play_ms = int((time.time() - t_play) * 1000)

                    # Log breakdown
                    total_ms = int((time.time() - t_total) * 1000)
                    self.logger.info("tts_profile %s", json.dumps({
                        "chars": len(text),
                        "synth_ms": synth_ms,
                        "play_ms": play_ms,
                        "total_ms": total_ms,
                        "overhead_ms": total_ms - synth_ms - play_ms
                    }))

                # Cleanup
                try:
                    os.unlink(tmp_wav.name)
                except Exception:
                    pass

                return True

            elif self.engine == "espeak":
                # Use espeak
                self.current_process = subprocess.Popen(
                    ["espeak", text],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.current_process.wait()
                return True

            else:
                # Simulate
                duration = len(text) * 0.05
                time.sleep(min(duration, 3.0))
                return True

        except Exception as e:
            self.logger.error("tts_failed %s", json.dumps({"error": str(e)}))
            return False
    def _check_interrupt(self) -> bool:
        """Check for interrupt signal by monitoring microphone for loud audio."""
        if not sd:
            return False
        try:
            # Quick 100ms audio sample
            audio = sd.rec(int(0.1 * 16000), samplerate=16000, channels=1, dtype="int16")
            sd.wait()
            # Calculate RMS volume
            rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2)) / 32768.0
            # If user is speaking loudly (above threshold), interrupt
            if rms > 0.05:
                self.logger.info("tts_interrupted %s", json.dumps({"rms": round(rms, 4)}))
                return True
            return False
        except Exception:
            return False
    def stop(self):
        """Stop current playback."""
        if self.current_process:
            try:
                self.current_process.terminate()
                self.current_process = None
            except Exception:
                pass


# =========================
# Conversation State Manager
# =========================

@dataclass
class ConversationState:
    """Manages multi-turn conversation state."""

    session_id: str
    turn_num: int = 0
    context_window: Deque[Tuple[str, str]] = field(default_factory=lambda: deque(maxlen=10))
    last_activity: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_turn(self, role: str, content: str):
        """Add a conversation turn."""
        self.turn_num += 1
        self.context_window.append((role, content))
        self.last_activity = time.time()

    def get_context(self, max_turns: int = 5) -> str:
        """Get formatted context."""
        recent = list(self.context_window)[-max_turns:]
        if not recent:
            return ""

        lines = []
        for role, content in recent:
            prefix = "User" if role == "user" else "Assistant"
            lines.append(f"{prefix}: {content}")

        return "\n".join(lines)

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """Check if conversation has expired."""
        return (time.time() - self.last_activity) > (timeout_minutes * 60)


# =========================
# Enhanced LLM Client
# =========================

class LLMClient:
    """Enhanced LLM client with context management."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        self.cfg = cfg.get("llm", {})
        self.logger = logger

        # Phase H: Enhanced model routing
        self.model_general = self.cfg.get("model", "deepseek-r1:7b")
        self.model_fallback = self.cfg.get("fallback_model", "llama3.2:3b")
        self.model_creative = self.cfg.get("creative_model", "deepseek-r1:7b")

        self.host = self.cfg.get("host", "http://127.0.0.1:11434")
        self.timeout = self.cfg.get("timeout_s", 20.0)
        self.max_context_turns = self.cfg.get("max_context_turns", 5)

        # Dev mode
        dev_cfg = cfg.get("dev", {})
        self.dev_enabled = dev_cfg.get("enabled", False)
        self.model_coder = dev_cfg.get("coder_model", "deepseek-coder:6.7b")

        # Load system prompt
        assistant_cfg = cfg.get("assistant", {})
        self.system_prompt = assistant_cfg.get("identity", "").strip()

        self.logger.info("llm_ready %s", json.dumps({
            "primary": self.model_general,
            "fallback": self.model_fallback,
            "creative": self.model_creative,
            "coder": self.model_coder if self.dev_enabled else None,
            "dev_enabled": self.dev_enabled
        }))

    def _strip_reasoning_tags(self, text: str) -> str:
        """Remove DeepSeek-R1 reasoning tokens from response."""
        import re
        # Strip <think>...</think> tags and their contents
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        # Also strip any remaining empty lines
        text = re.sub(r'\n\s*\n', '\n', text)
        return text.strip()

    def generate(self, prompt: str, context: Optional[str] = None,
                 model: Optional[str] = None, system: Optional[str] = None) -> str:
        """Generate response with context and fallback."""
        t0 = time.time()
        selected = model or self.model_general

        # Build full prompt
        full_prompt = self._build_prompt(prompt, context, system)

        # Try selected model
        try:
            response = self._call_ollama(selected, full_prompt)
            response = self._strip_reasoning_tags(response)
            self.logger.info("llm_done %s", json.dumps({
                "model": selected,
                "chars": len(response),
                "ms": int((time.time() - t0) * 1000)
            }))
            return response

        except Exception as e:
            # Fallback to general model
            if selected != self.model_general:
                self.logger.warning("llm_fallback %s", json.dumps({
                    "from": selected,
                    "to": self.model_general,
                    "err": str(e)
                }))

                try:
                    response = self._call_ollama(self.model_general, full_prompt)
                    response = self._strip_reasoning_tags(response)
                    self.logger.info("llm_done %s", json.dumps({
                        "model": self.model_general,
                        "chars": len(response),
                        "ms": int((time.time() - t0) * 1000)
                    }))
                    return response
                except Exception as e2:
                    self.logger.error("llm_failed %s", json.dumps({
                        "model": self.model_general,
                        "err": str(e2)
                    }))
            else:
                self.logger.error("llm_failed %s", json.dumps({
                    "model": selected,
                    "err": str(e)
                }))

            return "I'm having trouble processing that right now. Please try again."

    def _build_prompt(self, prompt: str, context: Optional[str] = None,
                      system: Optional[str] = None) -> str:
        """Build prompt with context and system message."""
        parts = []

        if system:
            parts.append(f"System: {system}")

        if context:
            parts.append(f"Context:\n{context}")

        parts.append(f"User: {prompt}")
        parts.append("Assistant:")

        return "\n\n".join(parts)

    def _call_ollama(self, model: str, prompt: str) -> str:
        """Call Ollama API."""
        if not requests:
            raise RuntimeError("requests library not available")

        url = f"{self.host}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "top_k": 40
            }
        }

        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()

        data = resp.json()
        return data.get("response", "").strip()


# =========================
# Enhanced Intent Router
# =========================

class IntentRouter:
    """Advanced intent routing with pattern matching."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger

        # Model mappings
        self.model_map = {
            "code": cfg.get("dev", {}).get("coder_model", "deepseek-coder:6.7b"),
            "general": cfg.get("llm", {}).get("model", "deepseek-r1:7b"),
            "creative": cfg.get("llm", {}).get("creative_model", "deepseek-r1:7b")
        }

        # Intent patterns
        self.patterns = {
            "code": [
                "code", "program", "script", "function", "debug", "error",
                "python", "javascript", "sql", "api", "implement", "algorithm"
            ],
            "creative": [
                "story", "poem", "write", "creative", "imagine", "describe",
                "narrative", "character", "plot"
            ],
            "system": [
                "system", "config", "setting", "preference", "mode", "status"
            ],
            "help": [
                "help", "how", "what", "explain", "tell me", "guide"
            ]
        }

    def route(self, text: str, conversation_state: Optional[ConversationState] = None) -> Tuple[str, str]:
        """Route to appropriate model and intent."""
        lower = text.lower()

        # Check for code patterns
        if any(word in lower.split() for word in self.patterns["code"]):
            intent = "code"
            model = self.model_map["code"]

        # Check for creative patterns
        elif any(word in lower for word in self.patterns["creative"]):
            intent = "creative"
            model = self.model_map["creative"]

        # Check for system commands
        elif any(word in lower for word in self.patterns["system"]):
            intent = "system"
            model = None  # Handle locally

        # Default to general
        else:
            intent = "general"
            model = self.model_map["general"]

        self.logger.info("intent_router %s", json.dumps({
            "intent": intent,
            "model": model
        }))

        return intent, model


# =========================
# Local Intent Handler
# =========================

class LocalIntentHandler:
    """Handle local intents without LLM."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger

    def handle(self, text: str, state: ConversationState) -> Optional[str]:
        """Handle local intents."""
        lower = text.lower().strip()
        # Sleep command (fuzzy matching for STT errors)
        sleep_variants = ["sleep nova", "go to sleep", "sleep mode", "good you sleep", "guided sleep", "goto sleep", "time to sleep"]
        if any(variant in lower for variant in sleep_variants) or (lower.strip() in ["sleep", "go sleep"]):
            return "Going to sleep mode."

        # Time/Date
        if "time" in lower and ("what" in lower or "tell" in lower):
            now = datetime.now()
            return f"It's {now.strftime('%I:%M %p')}."

        if "date" in lower and ("what" in lower or "tell" in lower):
            now = datetime.now()
            return f"Today is {now.strftime('%A, %B %d, %Y')}."

        # Weather (placeholder)
        if "weather" in lower:
            return "I'm running offline, so I can't check the weather right now."

        # System status
        if "status" in lower or "system" in lower:
            connected = self.cfg.get("connected", {}).get("enabled", False)
            dev_mode = self.cfg.get("dev", {}).get("enabled", False)

            status = []
            status.append(f"Mode: {'connected' if connected else 'offline'}")
            status.append(f"Dev: {'enabled' if dev_mode else 'disabled'}")
            status.append(f"Session: turn {state.turn_num}")

            return " | ".join(status)

        # Help
        if lower in ["help", "what can you do", "commands"]:
            return ("I can help with various tasks like answering questions, writing code, "
                   "creative writing, and general conversation. I also understand commands like "
                   "checking the time, date, or system status. Just ask naturally!")

        return None


# =========================
# Main Voice Loop
# =========================

class VoiceLoop:
    """Main orchestrator loop with all components integrated."""

    def __init__(self, cfg: Dict[str, Any], logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger

        # Core components
        self.memory = MemoryStore(MEMORY_DB, logger, cfg)
        self.audio_capture = AudioCapture(cfg, logger)
        self.wake_detector = WakeDetector(cfg, logger)
        self.stt = STT(cfg, logger)
        # P1.1: Create interrupt event BEFORE TTS
        self.interrupt_event = threading.Event()

        self.tts = TTS(cfg, logger, interrupt_event=self.interrupt_event)
        self.llm = LLMClient(cfg, logger)
        self.router = IntentRouter(cfg, logger)
        self.local_handler = LocalIntentHandler(cfg, logger)        # Conversation state - resume or create session
        session_timeout_hours = self.cfg.get("memory", {}).get("session_timeout_hours", 24)
        resumed_session = self.memory.get_latest_session(session_timeout_hours)

        if resumed_session:
            session_id = resumed_session
            session_info = self.memory.get_session_info(resumed_session)
            self.logger.info("session_resumed %s", json.dumps({
                "session_id": session_id,
                "timeout_hours": session_timeout_hours,
                "existing_turns": session_info.get("turn_count", 0),
                "started": session_info.get("started"),
                "last_activity": session_info.get("last_activity")
            }))
        else:
            session_id = f"session_{int(time.time())}"
            self.logger.info("session_created %s", json.dumps({
                "session_id": session_id
            }))

        self.state = ConversationState(session_id=session_id)

        # Mode
        self.mode = cfg.get("orchestrator", {}).get("mode", "text")

        # Flags
        self.asleep = False
        self.conversation_active = False
        self.running = True

        # Timing
        self.post_tts_until = 0.0
        self.grace_after_tts = cfg.get("tts", {}).get("grace_after_ms", 450) / 1000.0


        # P1.1: Keyboard listener for spacebar interrupt
        self._kb_listener = None
        if KEYBOARD_AVAILABLE:
            try:
                def on_press(key):
                    if key == keyboard.Key.space:
                        self.interrupt_event.set()
                        self.logger.info("spacebar_interrupt %s", json.dumps({"action": "set"}))
                self._kb_listener = keyboard.Listener(on_press=on_press)
                self._kb_listener.start()
                self.logger.info("keyboard_listener_started %s", json.dumps({"key": "spacebar"}))
            except Exception as e:
                self.logger.warning("keyboard_listener_failed %s", json.dumps({"error": str(e)}))

        self.conversation_timeout = self.cfg.get("orchestrator", {}).get("conversation_timeout_s", 30)
        self.last_turn_time = 0.0
    def _wait_for_wake(self) -> Optional[str]:
        """Wait for wake trigger."""
        # Check post-TTS grace period
        if time.time() < self.post_tts_until:
            time.sleep(min(0.1, self.post_tts_until - time.time()))
            return None

        if self.mode == "text":
            # Text mode
            self.logger.info("await_wake %s", json.dumps({"mode": "text"}))
            sys.stdout.write(">> ")
            sys.stdout.flush()

            try:
                line = sys.stdin.readline()
                if not line:
                    return None

                text = line.strip()
                if not text:
                    return None

                # Check for wake phrases
                lower = text.lower()
                for phrase in self.wake_detector.phrases:
                    if phrase in lower:
                        self.logger.info("wake_detected %s", json.dumps({
                            "engine": "text", "phrase": phrase
                        }))
                        # Remove wake phrase from text
                        text = text.lower().replace(phrase, "").strip()
                        if not text:
                            return "listening"
                        return text

                # Direct input in text mode
                return text

            except KeyboardInterrupt:
                self.running = False
                return None

        else:
            # Mic mode
            self.logger.info("await_wake %s", json.dumps({"mode": "mic"}))

            # Capture small audio buffer for wake detection
            audio = self.audio_capture.capture_until_silence(timeout=5.0)
            if audio is not None and self.wake_detector.detect_in_audio_stream(audio, threshold_override=None):
                # Flatten if 2D before transcribing wake buffer
                if audio.ndim > 1:
                    audio = audio.flatten()
                wake_text = self.stt.transcribe_audio(audio)
                # Reject false wake if STT returns empty
                if not wake_text or len(wake_text.strip()) == 0:
                    self.logger.warning("false_wake_rejected %s", json.dumps({
                        "reason": "empty_stt",
                        "threshold_used": 0.001 if self.asleep else 0.0005
                    }))
                    return None
                # Check if wake buffer contains sleep command
                if wake_text and any(phrase in wake_text.lower() for phrase in ["sleep nova", "go to sleep", "sleep mode", "good you sleep", "guided sleep", "goto sleep", "time to sleep", "sleep"]):
                    return wake_text  # Return actual text for sleep handler
                return "listening"
            return None

    def _is_whisper_hallucination(self, text: str) -> bool:
        """Filter Whisper hallucinations and background noise."""
        if not text or len(text.strip()) == 0:
            return True

        text = text.strip()
        lower = text.lower()

        # YouTube/streaming hallucinations
        hallucinations = [
            "thanks for watching", "thank you for watching",
            "please subscribe", "like and subscribe",
            "see you next time", "don't forget to subscribe",
            "goodbye", "bye bye", "music playing", "[music]"
        ]
        if any(phrase in lower for phrase in hallucinations):
            return True

        # Very short fragments (likely noise)
        words = text.split()
        if len(words) <= 2 and len(text) < 15:
            valid_short = ["time", "date", "help", "status", "stop", "sleep"]
            if not any(cmd in lower for cmd in valid_short):
                return True

        # Multiple short sentence fragments (TV commentary)
        sentences = [s.strip() for s in text.split('.') if s.strip()]
        if len(sentences) >= 3:
            avg_len = sum(len(s) for s in sentences) / len(sentences)
            if avg_len < 20:
                return True

        return False

    def _capture_user_input(self) -> Optional[str]:
        """Capture user input after wake."""
        if self.mode == "text":
            # Already captured in wake phase for text mode
            return None
        else:
            # Capture audio and transcribe
            audio = self.audio_capture.capture_until_silence(timeout=10.0)
            # Flatten if 2D (sounddevice returns [N,1] shape)
            if audio is not None and audio.ndim > 1:
                audio = audio.flatten()
                self.logger.info("audio_post_flatten %s", json.dumps({"shape": list(audio.shape), "ndim": audio.ndim}))
            if audio is not None:
                user_input = self.stt.transcribe_audio(audio)
                if self._is_whisper_hallucination(user_input):
                    self.logger.warning("whisper_hallucination_filtered %s", json.dumps({"text": user_input}))
                    return None
                return user_input
            return None

    def _process_turn(self, user_text: str):
        """Process a conversation turn."""
        t0 = time.time()

        # Log input
        self.logger.info("heard_text %s", json.dumps({
            "chars": len(user_text),
        }))

        self.logger.info("user_input_text: %s", user_text)
        # Add to conversation state
        self.state.add_turn("user", user_text)

        # Add user input to memory
        if self.memory.enabled:
            self.memory.add_turn(
                self.state.session_id,
                self.state.turn_num,
                "user",
                user_text,
                {}
            )

        # Check for local intent
        local_response = self.local_handler.handle(user_text, self.state)
        if local_response:
            self._respond(local_response, is_local=True)
            self._log_turn_timing(t0)
            return

        # Route to appropriate model
        intent, model = self.router.route(user_text, self.state)

        if intent == "system":
            # System commands handled locally
            response = "System command processed."
        else:
            # Prepare context
            context = self._prepare_context(user_text)

            # Generate response
            response = self.llm.generate(
                prompt=user_text,
                context=context,
                model=model,
                system=self.llm.system_prompt
            )

        # Respond
        self._respond(response, is_local=False)

        # Log timing
        self._log_turn_timing(t0)

    def _prepare_context(self, user_text: str) -> str:
        """Prepare context for LLM."""
        parts = []
        semantic_hits = []

        # Recent conversation context
        conv_context = self.state.get_context(self.llm.max_context_turns)
        if conv_context:
            parts.append("Recent conversation:")
            parts.append(conv_context)

        # Memory retrieval
        if self.memory.enabled:
            # Semantic search
            semantic_hits = self.memory.search_semantic(user_text, limit=self.memory.semantic_search_limit)
            if semantic_hits:
                parts.append("Relevant context:")
                for content, score in semantic_hits:
                    # Threshold already applied in search_semantic()
                    parts.append(f"- {content[:200]}")

        if parts:
            context = "\n".join(parts)
            self.logger.info("context_prepared %s", json.dumps({
                "chars": len(context),
                "has_conv": bool(conv_context),
                "semantic_hits": len(semantic_hits) if self.memory.enabled else 0
            }))
            return context

        return ""

    def _respond(self, response: str, is_local: bool = False):
        """Generate response with TTS."""
        # Add to state and memory
        self.state.add_turn("assistant", response)

        if self.memory.enabled:
            self.memory.add_turn(
                self.state.session_id,
                self.state.turn_num,
                "assistant",
                response,
                {"is_local": is_local}
            )

        # TTS
        self.tts.speak(response)

        # Update activity timestamp after TTS completes
        self.last_turn_time = time.time()

        # Set grace period
        self.post_tts_until = time.time() + self.grace_after_tts

    def _log_turn_timing(self, start_time: float):
        """Log turn timing metrics."""
        total_ms = int((time.time() - start_time) * 1000)
        tts_ms = self.tts.last_dur_ms

        self.logger.info("turn_timing %s", json.dumps({
            "total_ms": total_ms,
            "tts_ms": tts_ms,
            "turn": self.state.turn_num
        }))

    def run(self):
        """Main loop."""
        # Log startup
        self.logger.info("loop_start %s", json.dumps({
            "mode": self.mode,
            "session": self.state.session_id,
            "wake_phrases": self.wake_detector.phrases,
            "models": {
                "general": self.llm.model_general,
                "coder": self.llm.model_coder if self.llm.dev_enabled else None
            }
        }))

        # Main loop
        while self.running:
            try:
                # Handle sleep state
                if self.asleep:
                    wake = self._wait_for_wake()
                    if wake:
                        self.asleep = False
                        self.conversation_active = True
                        self.logger.info("conversation_active_set %s", json.dumps({"active": True, "reason": "wake_from_sleep"}))
                        self.last_turn_time = time.time()
                        self.tts.speak("Yes Sir, I'm awake")
                    continue

                # Check conversation timeout
                if self.conversation_active and self.last_turn_time > 0:
                    if time.time() - self.last_turn_time > self.conversation_timeout:
                        self.logger.info("conversation_timeout %s", json.dumps({
                            "elapsed_s": round(time.time() - self.last_turn_time, 1)
                        }))
                        self.conversation_active = False

                # Wait for wake or input based on conversation state
                if self.conversation_active:
                    # Already in conversation - just capture input directly
                    user_input = self._capture_user_input()
                    if not user_input:
                        continue
                else:
                    # Not in conversation - wait for wake word
                    user_input = self._wait_for_wake()

                if not user_input:
                    continue

                # Special case: just wake word
                if user_input == "listening":
                    # Capture actual input
                    if not self.conversation_active:
                        self.tts.speak("Yes Sir")
                        self.post_tts_until = time.time() + self.grace_after_tts
                        self.logger.info("conversation_active_set %s", json.dumps({"active": True, "reason": "initial_wake"}))
                        self.conversation_active = True
                    user_input = self._capture_user_input()
                    self.last_turn_time = time.time()
                    if not user_input:
                        continue

                # Check for sleep command
                if (("sleep" in user_input.lower() or "slip" in user_input.lower()) and ("no" in user_input.lower() or "nova" in user_input.lower() or "neva" in user_input.lower() or "nervo" in user_input.lower() or "nerva" in user_input.lower())) or "go to sleep" in user_input.lower():
                    self.logger.info("sleep_check %s", json.dumps({"text": user_input, "lower": user_input.lower(), "match": True}))
                    self.asleep = True
                    self.conversation_active = False
                    self.tts.speak("Going to sleep.")
                    self.logger.info("conversation_active_set %s", json.dumps({"active": False, "reason": "sleep_command"}))
                    self.post_tts_until = time.time() + self.grace_after_tts
                    continue

                # Process turn
                self._process_turn(user_input)

            except KeyboardInterrupt:
                self.running = False
                break
            except Exception as e:
                self.logger.error("loop_error %s", json.dumps({
                    "error": str(e),
                    "type": type(e).__name__
                }))
                # Continue loop despite errors

        # Cleanup
        self.logger.info("shutdown_requested %s", json.dumps({
            "reason": "user_interrupt",
            "session": self.state.session_id,
            "turns": self.state.turn_num
        }))

        # Stop any ongoing TTS
        self.tts.stop()


# =========================
# Entry Point
# =========================

def main():
    """Main entry point."""
    # Load config
    cfg = load_config()

    # Setup logging
    logger, log_path = ensure_logger(cfg.get("logging", {}))

    # Log boot
    log_event(logger, "boot", {
        "log_file": log_path,
        "time": now_iso(),
        "version": cfg.get("version", "v3"),
        "mode": cfg.get("orchestrator", {}).get("mode", "text")
    })

    # Log system capabilities
    log_event(logger, "capabilities", {
        "audio": AUDIO_BACKEND,
        "whisper": WHISPER_AVAILABLE,
        "oww": OWW_AVAILABLE,
        "embeddings": EMBEDDINGS_AVAILABLE
    })

    # Run loop
    try:
        loop = VoiceLoop(cfg, logger)
        loop.run()
    except KeyboardInterrupt:
        logger.info("shutdown_requested %s", json.dumps({"reason": "keyboard_interrupt"}))
    except Exception as e:
        logger.error("fatal_error %s", json.dumps({
            "error": str(e),
            "type": type(e).__name__
        }))
        raise


if __name__ == "__main__":
    main()
