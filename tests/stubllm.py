"""A scripted local LLM server speaking the OpenAI Chat Completions and Anthropic Messages wire formats.

Lets tests drive the REAL `openai` / `anthropic` SDKs end to end with no network and no cost:

    with StubLLM([("tool", "history_overview", {}), ("final", "Answer: {last}")]) as stub:
        cfg.llm.base_url = stub.url + "/v1"   # openai_compatible
        ...
        stub.requests  # every request body + headers, for assertions

Script steps: ("tool", name, args) asks for one tool call; ("final", text) ends the turn. "{last}" in the final text
is replaced with the content of the last tool result the client sent back.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self


class StubLLM:
    def __init__(self, script: list[tuple]):
        self.script = list(script)
        self.step = 0
        self.requests: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                stub.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                if "/chat/completions" in self.path:
                    out = stub._openai(body)
                elif self.path.split("?")[0].endswith("/v1/messages"):
                    out = stub._anthropic(body)
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> Self:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _next(self) -> tuple:
        s = self.script[min(self.step, len(self.script) - 1)]
        self.step += 1
        return s

    # ----- OpenAI Chat Completions
    def _openai(self, body: dict[str, Any]) -> dict[str, Any]:
        last = next((m.get("content", "") for m in reversed(body.get("messages", [])) if m.get("role") == "tool"), "")
        step = self._next()
        if step[0] == "tool":
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{self.step}", "type": "function",
                "function": {"name": step[1], "arguments": json.dumps(step[2])}}]}
            finish = "tool_calls"
        else:
            msg, finish = {"role": "assistant", "content": step[1].replace("{last}", str(last))}, "stop"
        return {"id": f"chatcmpl-{self.step}", "object": "chat.completion", "created": 0, "model": body.get("model"),
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}

    # ----- Anthropic Messages
    def _anthropic(self, body: dict[str, Any]) -> dict[str, Any]:
        last = ""
        msgs = body.get("messages", [])
        if msgs and isinstance(msgs[-1].get("content"), list):
            last = next((b.get("content", "") for b in msgs[-1]["content"] if b.get("type") == "tool_result"), "")
        step = self._next()
        if step[0] == "tool":
            content = [{"type": "tool_use", "id": f"toolu_{self.step}", "name": step[1], "input": step[2]}]
            stop = "tool_use"
        elif step[0] == "refusal":
            content, stop = [], "refusal"
        else:
            content, stop = [{"type": "text", "text": step[1].replace("{last}", str(last))}], "end_turn"
        out = {"id": f"msg_{self.step}", "type": "message", "role": "assistant", "model": body.get("model"),
               "content": content, "stop_reason": stop, "stop_sequence": None,
               "usage": {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 3}}
        if stop == "refusal":
            out["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": "declined"}
        return out
