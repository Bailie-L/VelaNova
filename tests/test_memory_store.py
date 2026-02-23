"""Tests for the inline MemoryStore class in voice_loop.py."""

from __future__ import annotations

import logging
import sqlite3
import tempfile
from pathlib import Path

import pytest

# Import directly from the orchestrator module
from orchestrator.voice_loop import MemoryStore


@pytest.fixture()
def tmp_db(tmp_path):
    """Return a temporary database path."""
    return tmp_path / "test_memory.db"


@pytest.fixture()
def logger():
    return logging.getLogger("test_memory")


@pytest.fixture()
def store(tmp_db, logger):
    """Create a MemoryStore with embeddings disabled."""
    cfg = {"memory": {"enabled": True, "max_history": 50, "semantic_threshold": 0.5}}
    return MemoryStore(tmp_db, logger, cfg)


class TestMemoryStoreInit:
    def test_creates_database_file(self, store, tmp_db):
        assert tmp_db.exists()

    def test_creates_conversations_table(self, store, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        assert "conversations" in tables

    def test_creates_fts_table(self, store, tmp_db):
        conn = sqlite3.connect(tmp_db)
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        assert "conversations_fts" in tables

    def test_disabled_store_skips_init(self, tmp_path, logger):
        db = tmp_path / "disabled.db"
        cfg = {"memory": {"enabled": False}}
        s = MemoryStore(db, logger, cfg)
        assert not s.enabled
        assert not db.exists()


class TestAddTurn:
    def test_add_and_retrieve(self, store, tmp_db):
        store.add_turn("sess1", 1, "user", "Hello there")
        store.add_turn("sess1", 2, "assistant", "Hi! How can I help?")

        turns = store.get_recent_turns("sess1", limit=10)
        assert len(turns) == 2
        assert turns[0] == ("user", "Hello there")
        assert turns[1] == ("assistant", "Hi! How can I help?")

    def test_recent_turns_respects_limit(self, store):
        for i in range(10):
            store.add_turn("sess1", i, "user", f"Message {i}")

        turns = store.get_recent_turns("sess1", limit=3)
        assert len(turns) == 3
        # Should be the last 3 turns in chronological order
        assert turns[-1][1] == "Message 9"

    def test_sessions_are_isolated(self, store):
        store.add_turn("sess_a", 1, "user", "From A")
        store.add_turn("sess_b", 1, "user", "From B")

        turns_a = store.get_recent_turns("sess_a")
        turns_b = store.get_recent_turns("sess_b")
        assert len(turns_a) == 1
        assert len(turns_b) == 1
        assert turns_a[0][1] == "From A"
        assert turns_b[0][1] == "From B"

    def test_disabled_store_returns_empty(self, tmp_path, logger):
        cfg = {"memory": {"enabled": False}}
        s = MemoryStore(tmp_path / "x.db", logger, cfg)
        s.add_turn("s", 1, "user", "test")
        assert s.get_recent_turns("s") == []


class TestFTSSearch:
    def test_fts_search_finds_content(self, store):
        store.add_turn("sess1", 1, "user", "The weather in London is rainy")
        store.add_turn("sess1", 2, "user", "I like sunny days in Paris")

        results = store.search_fts("London", limit=5)
        assert len(results) >= 1
        assert any("London" in r for r in results)

    def test_fts_search_no_results(self, store):
        store.add_turn("sess1", 1, "user", "Hello world")
        results = store.search_fts("xyznonexistent", limit=5)
        assert results == []


class TestSessionManagement:
    def test_get_latest_session(self, store):
        store.add_turn("session_one", 1, "user", "A message")

        # Use large max_age to avoid UTC vs local-time mismatch
        latest = store.get_latest_session(max_age_hours=24)
        assert latest == "session_one"

    def test_get_session_info(self, store):
        store.add_turn("sess1", 1, "user", "Hello")
        store.add_turn("sess1", 2, "assistant", "Hi")
        store.add_turn("sess1", 3, "user", "Bye")

        info = store.get_session_info("sess1")
        assert info["turn_count"] == 3
