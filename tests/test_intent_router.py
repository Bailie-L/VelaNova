"""Tests for IntentRouter and LocalIntentHandler from voice_loop.py."""

from __future__ import annotations

import logging

import pytest

from orchestrator.voice_loop import IntentRouter, LocalIntentHandler, ConversationState


@pytest.fixture()
def logger():
    return logging.getLogger("test_intent")


@pytest.fixture()
def cfg():
    return {
        "llm": {"model": "deepseek-r1:7b", "creative_model": "deepseek-r1:7b"},
        "dev": {"coder_model": "deepseek-coder:6.7b", "enabled": False},
        "connected": {"enabled": False},
    }


@pytest.fixture()
def router(cfg, logger):
    return IntentRouter(cfg, logger)


@pytest.fixture()
def handler(cfg, logger):
    return LocalIntentHandler(cfg, logger)


@pytest.fixture()
def state():
    return ConversationState(session_id="test-session")


class TestIntentRouter:
    def test_code_intent(self, router):
        intent, model = router.route("Write a python function to sort a list")
        assert intent == "code"
        assert "coder" in model or "code" in model

    def test_creative_intent(self, router):
        intent, model = router.route("Write me a short story about dragons")
        assert intent == "creative"

    def test_system_intent(self, router):
        intent, model = router.route("Check the system status")
        assert intent == "system"
        assert model is None

    def test_general_intent(self, router):
        intent, model = router.route("What is the capital of France?")
        assert intent == "general"
        assert model is not None

    def test_code_keywords(self, router):
        code_phrases = [
            "debug this error",
            "write a script",
            "implement an algorithm",
            "fix the python code",
        ]
        for phrase in code_phrases:
            intent, _ = router.route(phrase)
            assert intent == "code", f"Expected 'code' for: {phrase}"

    def test_creative_keywords(self, router):
        creative_phrases = [
            "tell me a story",
            "write a poem",
            "imagine a world",
        ]
        for phrase in creative_phrases:
            intent, _ = router.route(phrase)
            assert intent == "creative", f"Expected 'creative' for: {phrase}"


class TestLocalIntentHandler:
    def test_sleep_command(self, handler, state):
        result = handler.handle("go to sleep", state)
        assert result is not None
        assert "sleep" in result.lower()

    def test_time_query(self, handler, state):
        result = handler.handle("what time is it", state)
        assert result is not None
        # Should contain AM or PM
        assert "AM" in result or "PM" in result

    def test_date_query(self, handler, state):
        result = handler.handle("what is the date", state)
        assert result is not None
        assert "202" in result  # Contains a year

    def test_weather_offline(self, handler, state):
        result = handler.handle("what's the weather like", state)
        assert result is not None
        assert "offline" in result.lower()

    def test_help_command(self, handler, state):
        result = handler.handle("help", state)
        assert result is not None
        assert "task" in result.lower() or "help" in result.lower() or "question" in result.lower()

    def test_unhandled_returns_none(self, handler, state):
        result = handler.handle("Tell me about quantum physics", state)
        assert result is None

    def test_system_status(self, handler, state):
        result = handler.handle("show system status", state)
        assert result is not None
        assert "Mode" in result or "mode" in result.lower()
