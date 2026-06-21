from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MOCK_REPORT = {
    "project_name": "Streamlit E2E Smoke",
    "monitoring_company": "City Safety IoT",
    "report_number": "SMOKE-001",
    "monitoring_period": "2026-06-07",
    "monitoring_date": "2026-06-07",
    "interval_days": None,
    "thresholds": [],
    "summary_items": [],
    "tables": [],
    "conclusion": "Local mock response for UI and packaging smoke tests.",
}


def _mock_content(payload: dict) -> str:
    request_text = json.dumps(payload, ensure_ascii=False)
    if "建筑变形监测数据审核专家" in request_text:
        return json.dumps(
            [{"error_idx": 0, "verdict": "confirm", "reason": "mock ok", "suspected_origin": "report"}],
            ensure_ascii=False,
        )
    if "提取所有监测数据表格" in request_text or "输出JSON结构" in request_text:
        return json.dumps(MOCK_REPORT, ensure_ascii=False)
    return "本地 mock 最终审核通过。"


def build_mock_http_response(payload: dict) -> tuple[str, bytes]:
    """构造 OpenAI 兼容响应，同时覆盖普通 JSON 与 SSE 流式协议。"""
    content = _mock_content(payload)
    if payload.get("stream"):
        event = {
            "id": "chatcmpl-smoke",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": content},
                    "finish_reason": None,
                }
            ],
        }
        body = (
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            "data: [DONE]\n\n"
        ).encode("utf-8")
        return "text/event-stream; charset=utf-8", body

    response = {
        "id": "chatcmpl-smoke",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    return "application/json; charset=utf-8", json.dumps(response, ensure_ascii=False).encode("utf-8")


class MockOpenAIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {}

        content_type, raw = build_mock_http_response(payload)
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-cache")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args: object) -> None:
        print(fmt % args, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local OpenAI-compatible mock server for smoke tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MockOpenAIHandler)
    print(f"Mock OpenAI server listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
