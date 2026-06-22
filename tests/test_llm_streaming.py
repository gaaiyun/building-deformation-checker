from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stream_event(*, content: str | None = None, reasoning: str | None = None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def test_call_chat_completion_streams_and_joins_content(monkeypatch):
    import src.config as cfg
    from src.utils import llm_client

    captured: dict[str, object] = {}
    events = iter([
        _stream_event(reasoning="internal"),
        _stream_event(content='{"tables":'),
        _stream_event(content="[]}"),
    ])

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return events

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )
    monkeypatch.setattr(llm_client, "create_openai_client", lambda **_: fake_client)
    monkeypatch.setattr(cfg, "LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(cfg, "LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_USE_CACHE", "0")

    result = llm_client.call_chat_completion(
        [{"role": "user", "content": "extract"}],
        stream=True,
        max_retries=0,
    )

    assert result == '{"tables":[]}'
    assert captured["stream"] is True


def test_stream_response_close_called_when_available(monkeypatch):
    """resp.close() 必须在流式迭代后调用，防止连接泄漏。"""
    import src.config as cfg
    from src.utils import llm_client

    close_called = []

    class ClosableStream:
        def __init__(self):
            self._events = iter([_stream_event(content="ok")])
        def __iter__(self):
            return self._events.__iter__()
        def __next__(self):
            return next(self._events)
        def close(self):
            close_called.append(True)

    class FakeCompletions:
        def create(self, **kwargs):
            return ClosableStream()

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions()),
    )
    monkeypatch.setattr(llm_client, "create_openai_client", lambda **_: fake_client)
    monkeypatch.setattr(cfg, "LLM_MODEL", "test-model")
    monkeypatch.setattr(cfg, "LLM_BASE_URL", "https://test")
    monkeypatch.setenv("LLM_USE_CACHE", "0")

    result = llm_client.call_chat_completion(
        [{"role": "user", "content": "x"}], stream=True, max_retries=0,
    )
    assert result == "ok"
    assert close_called, "resp.close() was not called after streaming"


def test_openai_client_closed_after_call(monkeypatch):
    """每次调用创建的 OpenAI/httpx 客户端必须在函数退出前关闭。"""
    import src.config as cfg
    from src.utils import llm_client

    close_called = []

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                    ),
                ),
            )

        def close(self):
            close_called.append(True)

    monkeypatch.setattr(llm_client, "create_openai_client", lambda **_: FakeClient())
    monkeypatch.setattr(cfg, "LLM_MODEL", "test-model")
    monkeypatch.setattr(cfg, "LLM_BASE_URL", "https://test")
    monkeypatch.setenv("LLM_USE_CACHE", "0")

    result = llm_client.call_chat_completion(
        [{"role": "user", "content": "x"}], stream=False, max_retries=0,
    )
    assert result == "ok"
    assert close_called, "OpenAI client was not closed after completion"


def test_structured_parser_enables_streaming(monkeypatch):
    from src.tools import llm_parser

    captured: dict[str, object] = {}

    def fake_call(messages, **kwargs):
        captured.update(kwargs)
        return '{"project_name":"x","tables":[]}'

    monkeypatch.setattr(llm_parser, "call_chat_completion", fake_call)

    parsed = llm_parser._parse_chunk_with_llm(0, 1, "sample")

    assert parsed == {"project_name": "x", "tables": []}
    assert captured["stream"] is True
