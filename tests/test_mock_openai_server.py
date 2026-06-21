from __future__ import annotations

import json

from scripts.mock_openai_server import build_mock_http_response


def test_mock_openai_server_returns_sse_for_streaming_requests():
    content_type, body = build_mock_http_response({
        "stream": True,
        "messages": [{"role": "user", "content": "提取所有监测数据表格"}],
    })

    assert content_type.startswith("text/event-stream")
    assert body.endswith(b"data: [DONE]\n\n")
    first_event = body.split(b"\n\n", 1)[0].removeprefix(b"data: ")
    payload = json.loads(first_event.decode("utf-8"))
    assert payload["choices"][0]["delta"]["content"]


def test_mock_openai_server_keeps_non_streaming_compatibility():
    content_type, body = build_mock_http_response({
        "messages": [{"role": "user", "content": "最终审核"}],
    })

    assert content_type.startswith("application/json")
    payload = json.loads(body.decode("utf-8"))
    assert payload["choices"][0]["message"]["content"] == "本地 mock 最终审核通过。"
