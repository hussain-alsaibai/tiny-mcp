#!/usr/bin/env python3
"""
tiny-mcp: A zero-dependency, single-file MCP (Model Context Protocol) server.

Implements the Model Context Protocol over stdio and SSE transports,
supporting tools, resources, prompts, progress notifications, and logging.

Protocol version: 2024-11-05
Python version: 3.8+
Dependencies: None (stdlib only)
"""

from __future__ import annotations

import json
import logging
import re
import sys
import traceback
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, is_dataclass, asdict
from enum import Enum
from functools import wraps
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
from typing import Any, Dict, List, Optional, Union


# ─── Protocol Constants ──────────────────────────────────────────────────────

PROTOCOL_VERSION = "2024-11-05"


class ErrorCode(int, Enum):
    """JSON-RPC and MCP error codes."""
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    SERVER_ERROR_START = -32099
    SERVER_ERROR_END = -32000


# ─── Data Models ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TextContent:
    """Text content block for tool results."""
    type: str = "text"
    text: str = ""

    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text}


@dataclass(frozen=True)
class ImageContent:
    """Image content block for tool results."""
    type: str = "image"
    data: str = ""        # base64-encoded
    mimeType: str = ""    # e.g. image/png

    def to_dict(self) -> dict:
        return {"type": self.type, "data": self.data, "mimeType": self.mimeType}


@dataclass(frozen=True)
class EmbeddedResource:
    """Embedded resource content block for tool results."""
    type: str = "resource"
    resource: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "resource": self.resource}


Content = Union[TextContent, ImageContent, EmbeddedResource]


@dataclass
class Tool:
    """Registered tool descriptor."""
    name: str
    description: str
    input_schema: dict
    func: Callable[..., Any]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class Resource:
    """Registered resource descriptor."""
    uri: str
    name: str
    description: Optional[str] = None
    mimeType: Optional[str] = None
    func: Optional[Callable[..., Union[str, bytes, dict]]] = None

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"uri": self.uri, "name": self.name}
        if self.description:
            d["description"] = self.description
        if self.mimeType:
            d["mimeType"] = self.mimeType
        return d


@dataclass
class Prompt:
    """Registered prompt descriptor."""
    name: str
    description: Optional[str] = None
    arguments: Optional[List[dict]] = None
    func: Optional[Callable[..., Union[str, List[dict]]]] = None

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"name": self.name}
        if self.description:
            d["description"] = self.description
        if self.arguments:
            d["arguments"] = self.arguments
        return d


# ─── Helpers ───────────────────────────────────────────────────────────────

def _make_jsonrpc(id_: Optional[Union[str, int]], result: Any = None,
                  error: Optional[dict] = None) -> dict:
    """Build a JSON-RPC 2.0 response object."""
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


def _make_error(id_: Optional[Union[str, int]], code: int, message: str,
                data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return _make_jsonrpc(id_, error=err)


def _to_json_serializable(obj: Any) -> Any:
    """Recursively convert dataclasses and known types to plain dicts/lists."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_to_json_serializable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _to_json_serializable(v) for k, v in obj.items()}
    return obj


def _build_schema_from_hints(func: Callable[..., Any]) -> dict:
    """Build a JSON Schema from type hints (best-effort for stdlib)."""
    import inspect
    import typing
    sig = inspect.signature(func)
    # Resolve string annotations to real types
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty:
            required.append(name)
        anno = hints.get(name, param.annotation)
        if anno is inspect.Parameter.empty:
            props[name] = {"type": "string"}
        elif isinstance(anno, type):
            if anno is str:
                props[name] = {"type": "string"}
            elif anno is int:
                props[name] = {"type": "integer"}
            elif anno is float:
                props[name] = {"type": "number"}
            elif anno is bool:
                props[name] = {"type": "boolean"}
            else:
                props[name] = {"type": "string"}
        elif getattr(anno, "__origin__", None) is list:
            props[name] = {"type": "array"}
        else:
            props[name] = {"type": "string"}
    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


# ─── Server Core ─────────────────────────────────────────────────────────────

class MCPServer:
    """Zero-dependency MCP server supporting stdio and SSE transports."""

    def __init__(self, name: str = "tiny-mcp", version: str = "1.0.0"):
        self.name = name
        self.version = version
        self.tools: dict[str, Tool] = {}
        self.resources: dict[str, Resource] = {}
        self.prompts: dict[str, Prompt] = {}
        self.progress_callbacks: dict[str, Callable[[int, Optional[int]], None]] = {}
        self.logger = logging.getLogger("tiny-mcp")

        # Handlers map
        self._handlers: dict[str, Callable] = {
            "initialize": self._handle_initialize,
            "initialized": self._handle_initialized,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "resources/list": self._handle_resources_list,
            "resources/read": self._handle_resources_read,
            "prompts/list": self._handle_prompts_list,
            "prompts/get": self._handle_prompts_get,
        }

    # ── Registration API ────────────────────────────────────────────────────

    def tool(self, name: Optional[str] = None, description: Optional[str] = None):
        """Decorator to register a tool.

        Usage::

            @server.tool()
            def hello(name: str) -> str:
                return f"Hello, {name}!"
        """
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            nonlocal name, description
            if name is None:
                name = func.__name__
            if description is None:
                description = (func.__doc__ or "").strip()
            schema = _build_schema_from_hints(func)
            self.tools[name] = Tool(
                name=name,
                description=description,
                input_schema=schema,
                func=func,
            )
            self.logger.debug("Registered tool: %s", name)
            return func
        return decorator

    def resource(self, uri: str, *, name: Optional[str] = None,
                 description: Optional[str] = None,
                 mimeType: Optional[str] = None):
        """Decorator to register a resource."""
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.resources[uri] = Resource(
                uri=uri,
                name=name or uri,
                description=description,
                mimeType=mimeType,
                func=func,
            )
            self.logger.debug("Registered resource: %s", uri)
            return func
        return decorator

    def prompt(self, name: str, *, description: Optional[str] = None,
               arguments: Optional[List[dict]] = None):
        """Decorator to register a prompt."""
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.prompts[name] = Prompt(
                name=name,
                description=description,
                arguments=arguments,
                func=func,
            )
            self.logger.debug("Registered prompt: %s", name)
            return func
        return decorator

    # ── Progress Notifications ──────────────────────────────────────────────

    def report_progress(self, progress_token: str, progress: int,
                        total: Optional[int] = None) -> None:
        """Send a progress notification if a callback is registered."""
        cb = self.progress_callbacks.get(progress_token)
        if cb:
            try:
                cb(progress, total)
            except Exception:
                self.logger.exception("Progress callback failed")

    # ── Request Handlers ────────────────────────────────────────────────────

    def _handle_initialize(self, params: dict) -> dict:
        client_version = params.get("protocolVersion", "unknown")
        self.logger.info("Client initializing with protocol %s", client_version)
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
                "logging": {},
                "progress": {},
            },
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def _handle_initialized(self, params: dict) -> dict:
        self.logger.info("Client confirmed initialization")
        return {}

    def _handle_tools_list(self, params: dict) -> dict:
        return {"tools": [t.to_dict() for t in self.tools.values()]}

    def _handle_tools_call(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not name or name not in self.tools:
            raise ValueError(f"Unknown tool: {name}")
        tool = self.tools[name]
        self.logger.debug("Calling tool %s with args %r", name, arguments)
        result = tool.func(**arguments)
        # Normalize result to list of content objects
        contents = self._normalize_tool_result(result)
        return {"content": contents, "isError": False}

    def _normalize_tool_result(self, result: Any) -> list[dict]:
        if result is None:
            return []
        if isinstance(result, str):
            return [TextContent(text=result).to_dict()]
        if isinstance(result, (TextContent, ImageContent, EmbeddedResource)):
            return [result.to_dict()]
        if isinstance(result, (list, tuple)):
            out: list[dict] = []
            for item in result:
                if isinstance(item, (TextContent, ImageContent, EmbeddedResource)):
                    out.append(item.to_dict())
                elif isinstance(item, dict):
                    out.append(item)
                else:
                    out.append(TextContent(text=str(item)).to_dict())
            return out
        if isinstance(result, dict):
            return [result]
        return [TextContent(text=str(result)).to_dict()]

    def _handle_resources_list(self, params: dict) -> dict:
        return {"resources": [r.to_dict() for r in self.resources.values()]}

    def _handle_resources_read(self, params: dict) -> dict:
        uri = params.get("uri")
        if not uri or uri not in self.resources:
            raise ValueError(f"Unknown resource: {uri}")
        res = self.resources[uri]
        if res.func is None:
            raise ValueError(f"Resource {uri} has no handler")
        data = res.func()
        if isinstance(data, bytes):
            import base64
            text = base64.b64encode(data).decode("ascii")
            return {"contents": [{"uri": uri, "mimeType": res.mimeType or "application/octet-stream",
                                   "blob": text}]}
        if isinstance(data, dict):
            return {"contents": [{"uri": uri, **data}]}
        return {"contents": [{"uri": uri, "mimeType": res.mimeType or "text/plain",
                               "text": str(data)}]}

    def _handle_prompts_list(self, params: dict) -> dict:
        return {"prompts": [p.to_dict() for p in self.prompts.values()]}

    def _handle_prompts_get(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not name or name not in self.prompts:
            raise ValueError(f"Unknown prompt: {name}")
        prompt = self.prompts[name]
        if prompt.func is None:
            raise ValueError(f"Prompt {name} has no handler")
        result = prompt.func(**arguments)
        messages: list[dict] = []
        if isinstance(result, str):
            messages.append({"role": "user", "content": {"type": "text", "text": result}})
        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    messages.append(item)
                else:
                    messages.append({"role": "user", "content": {"type": "text", "text": str(item)}})
        elif isinstance(result, dict):
            messages.append(result)
        else:
            messages.append({"role": "user", "content": {"type": "text", "text": str(result)}})
        return {"description": prompt.description or "", "messages": messages}

    # ── Core Dispatch ───────────────────────────────────────────────────────

    def handle_request(self, raw: Union[str, bytes]) -> Optional[dict]:
        """Process a single JSON-RPC request string."""
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _make_error(None, ErrorCode.PARSE_ERROR, str(exc))

        if isinstance(obj, list):
            # Batch requests – process each, return list
            return self._handle_batch(obj)

        return self._handle_single(obj)

    def _handle_single(self, obj: dict) -> Optional[dict]:
        if not isinstance(obj, dict):
            return _make_error(None, ErrorCode.INVALID_REQUEST,
                               "Request must be a JSON object")
        id_ = obj.get("id")
        method = obj.get("method")
        params = obj.get("params", {})

        if not method:
            return _make_error(id_, ErrorCode.INVALID_REQUEST, "Missing method")

        handler = self._handlers.get(method)
        if handler is None:
            return _make_error(id_, ErrorCode.METHOD_NOT_FOUND,
                               f"Method not found: {method}")

        try:
            result = handler(params)
        except Exception as exc:
            self.logger.exception("Error handling %s", method)
            return _make_error(id_, ErrorCode.INTERNAL_ERROR,
                               str(exc), data=traceback.format_exc())

        # Notifications have no id → no response
        if id_ is None:
            return None
        return _make_jsonrpc(id_, result=result)

    def _handle_batch(self, batch: list) -> Optional[list]:
        responses = []
        for item in batch:
            resp = self._handle_single(item)
            if resp is not None:
                responses.append(resp)
        return responses or None

    # ── Stdio Transport ─────────────────────────────────────────────────────

    def run_stdio(self) -> None:
        """Run the server over standard input/output (default transport)."""
        self.logger.info("tiny-mcp stdio transport starting")
        try:
            while True:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                resp = self.handle_request(line)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        except Exception:
            self.logger.exception("Fatal error in stdio transport")
            raise

    # ── SSE Transport ───────────────────────────────────────────────────────

    def run_sse(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        """Run the server over SSE (Server-Sent Events) using stdlib only."""
        self.logger.info("tiny-mcp SSE transport starting on %s:%d", host, port)
        server = self._build_sse_server(host, port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            self.logger.info("Interrupted by user")
        finally:
            server.server_close()

    def _build_sse_server(self, host: str, port: int) -> HTTPServer:
        """Build and return an HTTPServer configured for SSE."""
        server_self = self

        class SSEHandler(BaseHTTPRequestHandler):
            """Handle SSE connections and POST requests."""

            def log_message(self, fmt: str, *args: Any) -> None:
                server_self.logger.debug(fmt % args)

            def do_GET(self) -> None:
                if self.path == "/sse":
                    self._send_sse()
                elif self.path == "/":
                    self._send_index()
                else:
                    self.send_error(404)

            def _send_index(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                html = (
                    "<!DOCTYPE html><html><head><title>tiny-mcp</title></head>"
                    "<body><h1>tiny-mcp SSE Server</h1>"
                    "<p>Connect to <code>/sse</code> for events.</p>"
                    "<p>POST JSON-RPC to <code>/message</code></p></body></html>"
                )
                self.wfile.write(html.encode("utf-8"))

            def _send_sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                # In a full implementation, this would queue messages.
                # Here we keep the connection open until closed.
                try:
                    while True:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        import time
                        time.sleep(30)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self) -> None:
                if self.path != "/message":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                resp = server_self.handle_request(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if resp is not None:
                    self.wfile.write(json.dumps(resp).encode("utf-8"))
                else:
                    self.wfile.write(b"{}")

        return HTTPServer((host, port), SSEHandler)


# ─── Convenience Entrypoint ──────────────────────────────────────────────────

def main() -> None:
    """CLI entrypoint: run with stdio transport by default."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    server = MCPServer()
    # Example builtin tool
    @server.tool(description="Return a greeting")
    def hello(name: str = "World") -> str:
        return f"Hello, {name}!"

    server.run_stdio()


if __name__ == "__main__":
    main()
