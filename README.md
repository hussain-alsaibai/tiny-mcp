# tiny-mcp

> A zero-dependency, single-file MCP (Model Context Protocol) server in pure Python.

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](tiny_mcp.py)

---

## Features

- **Zero external dependencies** — uses only the Python standard library
- **Single file** — drop `tiny_mcp.py` into any project
- **JSON-RPC 2.0** — full protocol compliance
- **Protocol version 2024-11-05** — latest MCP spec support
- **Stdio transport** — default, works with any MCP client
- **SSE transport** — optional, powered by `http.server`
- **Tool registration** — simple `@server.tool()` decorator
- **Resource support** — register and serve resources by URI
- **Prompt templates** — register parameterized prompts
- **Progress notifications** — report long-running operation progress
- **Production logging** — structured logging with the stdlib
- **Error handling** — robust JSON-RPC error codes and tracebacks

---

## Quick Start

```python
from tiny_mcp import MCPServer

server = MCPServer(name="my-server", version="1.0.0")

@server.tool(description="Echo a message back")
def echo(message: str) -> str:
    return message

@server.resource("file:///greeting.txt", mimeType="text/plain")
def greeting() -> str:
    return "Hello from tiny-mcp!"

@server.prompt("greet", description="A friendly greeting prompt")
def greet_prompt() -> str:
    return "Say hello to the user!"

# Run over stdio (default MCP transport)
server.run_stdio()
```

---

## Tool Registration Example

Tools are registered with the `@server.tool()` decorator. The input schema is automatically inferred from type hints.

```python
from tiny_mcp import MCPServer

server = MCPServer()

@server.tool(description="Add two integers")
def add(a: int, b: int) -> int:
    return a + b

@server.tool(description="Concatenate strings")
def concat(prefix: str, suffix: str) -> str:
    return prefix + suffix

# Tools can return:
#   - str → converted to text content
#   - list → multiple content blocks
#   - TextContent / ImageContent / EmbeddedResource → explicit types
```

### Custom Content Types

```python
from tiny_mcp import TextContent, ImageContent

@server.tool(description="Return an image")
def chart():
    return ImageContent(data="<base64>", mimeType="image/png")
```

---

## SSE Transport Example

 tiny-mcp includes an optional SSE (Server-Sent Events) transport built on `http.server`. No external web framework needed.

```python
from tiny_mcp import MCPServer

server = MCPServer()

@server.tool(description="Get current time")
def time() -> str:
    from datetime import datetime
    return datetime.now().isoformat()

# Start SSE server on localhost:8080
server.run_sse(host="127.0.0.1", port=8080)
```

Access:
- `GET /sse` — SSE event stream
- `POST /message` — JSON-RPC requests
- `GET /` — status page

---

## Comparison with Other MCP Frameworks

| Feature | **tiny-mcp** | mcp-python-sdk |
|--------|--------------|----------------|
| Dependencies | **Zero** | `anyio`, `httpx`, `pydantic`, etc. |
| File count | **1** | Multiple packages |
| Python version | **3.8+** | 3.10+ |
| Transports | stdio, SSE | stdio, SSE, HTTP |
| Auto schema | ✅ | ✅ |
| Production ready | ✅ | ✅ |

**Use tiny-mcp when:**
- You want the smallest possible footprint
- You cannot install external packages
- You need to embed an MCP server in an existing codebase
- You prefer stdlib-only solutions

**Use mcp-python-sdk when:**
- You need the full feature set (sampling, roots, etc.)
- You are already using async/await heavily
- You want official first-party support

---

## Architecture

```
tiny_mcp.py  (single file, ~400 lines)
├── MCPServer
│   ├── tool()      — decorator registration
│   ├── resource()  — URI-based content
│   ├── prompt()    — prompt templates
│   └── run_stdio() / run_sse()
├── JSON-RPC 2.0 protocol
├── Content types (Text, Image, Resource)
└── Error codes & logging
```

---

## Testing

```bash
# Run all tests
python -m pytest test_tiny_mcp.py -v

# Or with unittest directly
python test_tiny_mcp.py
```

Coverage includes:
- Protocol handshake (`initialize` / `initialized`)
- Tool registration, listing, and calling
- Resource listing and reading
- Prompt listing and retrieval
- Batch JSON-RPC requests
- Error handling (parse errors, unknown methods, tool exceptions)
- Progress notifications
- Content type normalization

---

## Benchmarking

```bash
python benchmark.py
```

Runs a simple throughput test measuring messages processed per second.

---

## Requirements

- Python 3.8 or newer
- Zero external packages

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Contributing

 tiny-mcp intentionally stays minimal. For bug reports or small improvements, open an issue. For new features, consider whether they can be achieved without adding dependencies.
