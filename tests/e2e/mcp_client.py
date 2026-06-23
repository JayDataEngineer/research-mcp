"""Minimal MCP JSON-RPC client for e2e tests.

Handles both stateless (FastMCP stateless_http=True) and stateful (C# SDK,
returns Mcp-Session-Id) servers. Uses httpx for proper streaming/SSE support
(the server's transcribe_audio call returns a large SSE response that
urllib truncates with IncompleteRead).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx


@dataclass
class ToolResult:
    success: bool          # the server returned a valid MCP result (not a JSON-RPC error)
    tool_success: bool | None  # the tool's own structuredContent.success flag (None if not structured)
    content: dict          # raw result.content (or error)
    structured: dict       # result.structuredContent (or {})
    raw: dict              # full parsed JSON-RPC message
    error: str | None      # MCP-level error message (JSON-RPC error.code/message)


class McpClient:
    def __init__(self, url: str, timeout: int = 180):
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._init_done = False
        # verify=False because tailscale serve uses an internal-CA cert that
        # the host trust store doesn't know about. The tailnet itself is the
        # auth boundary; TLS here is transport-only.
        self._client = httpx.Client(verify=False, timeout=httpx.Timeout(timeout, connect=15.0))

    def _post(self, body: dict, *, is_init: bool = False) -> tuple[dict, dict]:
        """POST a JSON-RPC message, return (parsed_data, response_headers)."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id and not is_init:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            r = self._client.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as e:
            return {"_http_error": str(e)[:300]}, {}
        raw = r.text
        headers_out = dict(r.headers)
        # Streamable HTTP responses may be SSE with multiple `data:` lines —
        # log/progress notifications (no `id`), then the actual result. Parse
        # each `data:` line as its own JSON message (standard SSE framing)
        # and pick the one whose JSON-RPC id matches the request. Otherwise
        # the client latches onto the first notification and sees an empty
        # result.
        req_id = body.get("id")
        best: dict | None = None
        last_parsed: dict | None = None
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            try:
                parsed = json.loads(payload)
            except Exception:
                continue
            last_parsed = parsed
            if req_id is not None and parsed.get("id") == req_id:
                best = parsed
                break
            # Remember the first message that has a result/error (skip pure
            # notifications which carry only `method`).
            if ("result" in parsed or "error" in parsed) and best is None:
                best = parsed
        if best is not None:
            return best, headers_out
        if last_parsed is not None:
            return last_parsed, headers_out
        try:
            return json.loads(raw), headers_out
        except Exception:
            return {"_parse_error": True, "_body": raw[:500]}, headers_out

    def initialize(self) -> dict:
        body = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "e2e-probe", "version": "1"},
            },
        }
        data, headers = self._post(body, is_init=True)
        # Session ID is returned as a response header (case-insensitive)
        sid = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
        if sid:
            self.session_id = sid
        self._init_done = True
        # Send initialized notification (stateful servers expect it, stateless
        # servers ignore it). Some servers (C# SDK) reject it — that's OK.
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, is_init=False)
        except Exception:
            pass
        return data

    def list_tools(self) -> list[dict]:
        data, _ = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return data.get("result", {}).get("tools", [])

    def call(self, tool_name: str, arguments: dict) -> ToolResult:
        body = {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        data, _ = self._post(body)
        if "error" in data:
            return ToolResult(
                success=False, tool_success=None, content={}, structured={},
                raw=data, error=f"{data['error'].get('code')}: {data['error'].get('message','')[:200]}",
            )
        result = data.get("result", {})
        content = result.get("content", [])
        structured = result.get("structuredContent", {})
        # Pull the success flag from structuredContent if present
        tool_success = structured.get("success") if isinstance(structured, dict) else None
        # If is_error was set on the result, treat as tool-level failure
        is_error = result.get("isError", False)
        if is_error:
            tool_success = False
        # Pull text content if no structuredContent. When isError=true the
        # server sends a human-readable message in content[0].text (per the
        # MCP spec) — surface it as structured.error so validators can match
        # against it (e.g. "disabled", "missing required argument").
        text_msg: str | None = None
        if content:
            for c in content:
                if c.get("type") == "text":
                    txt = c.get("text", "")
                    try:
                        parsed = json.loads(txt)
                        if isinstance(parsed, dict):
                            if not structured:
                                structured = parsed
                            if tool_success is None:
                                tool_success = parsed.get("success")
                            continue
                    except Exception:
                        pass
                    # Non-JSON text — keep as the message
                    if not structured:
                        text_msg = txt
        if is_error and not structured:
            structured = {"success": False, "error": text_msg or "tool returned isError with no message"}
        elif text_msg and isinstance(structured, dict) and "error" not in structured:
            structured.setdefault("error", text_msg)
        return ToolResult(
            success=True,
            tool_success=tool_success,
            content=content,
            structured=structured,
            raw=data,
            error=None,
        )
