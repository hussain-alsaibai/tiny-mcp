"""Comprehensive tests for tiny_mcp.py.

Run with: python -m pytest test_tiny_mcp.py -v
Or:       python test_tiny_mcp.py          (unittest runner)
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
import unittest
from io import StringIO

# Add parent to path so we can import tiny_mcp
sys.path.insert(0, "..")

from tiny_mcp import (
    MCPServer,
    TextContent,
    ImageContent,
    EmbeddedResource,
    ErrorCode,
    PROTOCOL_VERSION,
)


class TestProtocolHandshake(unittest.TestCase):
    """Test JSON-RPC / MCP initialization handshake."""

    def setUp(self) -> None:
        self.server = MCPServer(name="test-server", version="0.0.1")

    def _send(self, req: dict) -> dict:
        raw = json.dumps(req)
        resp = self.server.handle_request(raw)
        self.assertIsNotNone(resp)
        return resp  # type: ignore[return-value]

    def test_initialize(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION},
        }
        resp = self._send(req)
        self.assertEqual(resp.get("jsonrpc"), "2.0")
        self.assertEqual(resp.get("id"), 1)
        self.assertIn("result", resp)
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(result["serverInfo"]["name"], "test-server")
        self.assertIn("capabilities", result)

    def test_initialized_notification(self) -> None:
        # Notifications have no id → no response
        req = {
            "jsonrpc": "2.0",
            "method": "initialized",
            "params": {},
        }
        raw = json.dumps(req)
        resp = self.server.handle_request(raw)
        self.assertIsNone(resp)

    def test_parse_error(self) -> None:
        resp = self.server.handle_request("not json{{{")
        self.assertIsNotNone(resp)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], ErrorCode.PARSE_ERROR)

    def test_invalid_request_missing_method(self) -> None:
        req = {"jsonrpc": "2.0", "id": 42}
        resp = self._send(req)
        self.assertEqual(resp["error"]["code"], ErrorCode.INVALID_REQUEST)

    def test_method_not_found(self) -> None:
        req = {"jsonrpc": "2.0", "id": 7, "method": "nonexistent"}
        resp = self._send(req)
        self.assertEqual(resp["error"]["code"], ErrorCode.METHOD_NOT_FOUND)


class TestToolRegistration(unittest.TestCase):
    """Test tool decorator and listing/calling."""

    def setUp(self) -> None:
        self.server = MCPServer()

        @self.server.tool(description="Echo back the input")
        def echo(message: str) -> str:
            return message

        @self.server.tool()
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        @self.server.tool()
        def multi_return() -> list:
            return ["first", "second"]

    def test_tools_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        tools = resp["result"]["tools"]  # type: ignore[index]
        names = {t["name"] for t in tools}
        self.assertIn("echo", names)
        self.assertIn("add", names)
        self.assertIn("multi_return", names)

    def test_tool_call_str_result(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hi"}},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        content = resp["result"]["content"]  # type: ignore[index]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[0]["text"], "hi")

    def test_tool_call_int_result(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "add", "arguments": {"a": 2, "b": 3}},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        content = resp["result"]["content"]  # type: ignore[index]
        self.assertEqual(content[0]["text"], "5")

    def test_tool_call_unknown_tool(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "missing", "arguments": {}},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        self.assertEqual(resp["error"]["code"], ErrorCode.INTERNAL_ERROR)

    def test_tool_schema_from_hints(self) -> None:
        tool = self.server.tools["add"]
        schema = tool.input_schema
        self.assertEqual(schema["type"], "object")
        self.assertIn("a", schema["properties"])
        self.assertIn("b", schema["properties"])
        self.assertEqual(schema["properties"]["a"]["type"], "integer")
        self.assertEqual(schema["required"], ["a", "b"])


class TestResourceHandling(unittest.TestCase):
    """Test resource registration, listing, and reading."""

    def setUp(self) -> None:
        self.server = MCPServer()

        @self.server.resource("file:///notes.txt", name="notes",
                              description="My notes", mimeType="text/plain")
        def notes() -> str:
            return "These are my notes."

        @self.server.resource("file:///data.json", name="data",
                              mimeType="application/json")
        def data() -> dict:
            return {"key": "value", "count": 42}

    def test_resources_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}}
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        resources = resp["result"]["resources"]  # type: ignore[index]
        uris = {r["uri"] for r in resources}
        self.assertIn("file:///notes.txt", uris)
        self.assertIn("file:///data.json", uris)

    def test_resource_read_text(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/read",
            "params": {"uri": "file:///notes.txt"},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        contents = resp["result"]["contents"]  # type: ignore[index]
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["text"], "These are my notes.")
        self.assertEqual(contents[0]["mimeType"], "text/plain")

    def test_resource_read_dict(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/read",
            "params": {"uri": "file:///data.json"},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        contents = resp["result"]["contents"]  # type: ignore[index]
        self.assertEqual(contents[0]["key"], "value")

    def test_resource_read_unknown(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/read",
            "params": {"uri": "file:///missing"},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        self.assertEqual(resp["error"]["code"], ErrorCode.INTERNAL_ERROR)


class TestPromptHandling(unittest.TestCase):
    """Test prompt registration, listing, and retrieval."""

    def setUp(self) -> None:
        self.server = MCPServer()

        @self.server.prompt("summarize", description="Summarize a document")
        def summarize() -> str:
            return "Please summarize the following document: ..."

        @self.server.prompt("code-review", description="Review code changes",
                             arguments=[{"name": "language", "required": True}])
        def code_review(language: str = "python") -> list:
            return [
                {"role": "system", "content": {"type": "text", "text": "You are a code reviewer."}},
                {"role": "user", "content": {"type": "text", "text": f"Review this {language} code."}},
            ]

    def test_prompts_list(self) -> None:
        req = {"jsonrpc": "2.0", "id": 1, "method": "prompts/list", "params": {}}
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        prompts = resp["result"]["prompts"]  # type: ignore[index]
        names = {p["name"] for p in prompts}
        self.assertIn("summarize", names)
        self.assertIn("code-review", names)

    def test_prompt_get_str(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "prompts/get",
            "params": {"name": "summarize"},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        messages = resp["result"]["messages"]  # type: ignore[index]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

    def test_prompt_get_list(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "prompts/get",
            "params": {"name": "code-review", "arguments": {"language": "rust"}},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        messages = resp["result"]["messages"]  # type: ignore[index]
        self.assertEqual(len(messages), 2)
        self.assertIn("rust", messages[1]["content"]["text"])


class TestProgressNotifications(unittest.TestCase):
    """Test progress notification callbacks."""

    def test_progress_callback(self) -> None:
        server = MCPServer()
        calls: list[tuple[int, int | None]] = []

        def cb(progress: int, total: int | None) -> None:
            calls.append((progress, total))

        token = "test-token"
        server.progress_callbacks[token] = cb
        server.report_progress(token, 50, 100)
        server.report_progress(token, 100, 100)
        self.assertEqual(calls, [(50, 100), (100, 100)])

    def test_progress_no_callback(self) -> None:
        server = MCPServer()
        # Should not raise
        server.report_progress("missing", 10)


class TestContentTypes(unittest.TestCase):
    """Test helper content classes."""

    def test_text_content(self) -> None:
        tc = TextContent(text="hello")
        self.assertEqual(tc.to_dict(), {"type": "text", "text": "hello"})

    def test_image_content(self) -> None:
        ic = ImageContent(data="abc123", mimeType="image/png")
        self.assertEqual(ic.to_dict()["type"], "image")

    def test_embedded_resource(self) -> None:
        er = EmbeddedResource(resource={"uri": "test"})
        self.assertEqual(er.to_dict()["type"], "resource")


class TestBatchRequests(unittest.TestCase):
    """Test JSON-RPC batch processing."""

    def setUp(self) -> None:
        self.server = MCPServer()

        @self.server.tool()
        def ping() -> str:
            return "pong"

    def test_batch(self) -> None:
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "ping", "arguments": {}}},
        ]
        raw = json.dumps(batch)
        resp = self.server.handle_request(raw)
        self.assertIsNotNone(resp)
        self.assertIsInstance(resp, list)
        self.assertEqual(len(resp), 2)
        self.assertEqual(resp[0]["id"], 1)
        self.assertEqual(resp[1]["id"], 2)


class TestErrorHandling(unittest.TestCase):
    """Test various error conditions."""

    def setUp(self) -> None:
        self.server = MCPServer()

        @self.server.tool()
        def crash() -> str:
            raise RuntimeError("boom")

    def test_tool_exception(self) -> None:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "crash", "arguments": {}},
        }
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNotNone(resp)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], ErrorCode.INTERNAL_ERROR)
        self.assertIn("boom", resp["error"]["message"])

    def test_notification_no_response(self) -> None:
        req = {"jsonrpc": "2.0", "method": "tools/list", "params": {}}
        resp = self.server.handle_request(json.dumps(req))
        self.assertIsNone(resp)


class TestTransportHelpers(unittest.TestCase):
    """Smoke tests for transport entrypoints."""

    def test_stdio_interrupt(self) -> None:
        server = MCPServer()
        # We cannot easily test full stdio loop, but ensure it exists
        self.assertTrue(callable(server.run_stdio))

    def test_sse_build(self) -> None:
        server = MCPServer()
        srv = server._build_sse_server("127.0.0.1", 0)
        self.assertIsNotNone(srv)
        srv.server_close()


class TestLogging(unittest.TestCase):
    """Test that logging integration works."""

    def test_logger_present(self) -> None:
        server = MCPServer()
        self.assertIsInstance(server.logger, logging.Logger)
        server.logger.setLevel(logging.DEBUG)
        self.assertTrue(server.logger.isEnabledFor(logging.DEBUG))


if __name__ == "__main__":
    unittest.main(verbosity=2)
