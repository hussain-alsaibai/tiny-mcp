#!/usr/bin/env python3
"""
Simple benchmark for tiny-mcp message throughput.

Measures how many JSON-RPC requests the server can process per second.

Usage:
    python benchmark.py
    python benchmark.py --iterations 100000 --warmup 1000
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from tiny_mcp import MCPServer


def create_server() -> MCPServer:
    """Create a benchmark server with a few registered tools."""
    server = MCPServer(name="benchmark-server", version="0.0.1")

    @server.tool(description="Echo back the input")
    def echo(message: str) -> str:
        return message

    @server.tool(description="Add two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    @server.tool(description="Return a greeting")
    def greet(name: str = "World") -> str:
        return f"Hello, {name}!"

    return server


def benchmark_tools_list(server: MCPServer, iterations: int) -> float:
    """Benchmark tools/list requests. Returns seconds elapsed."""
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    raw = json.dumps(req)
    start = time.perf_counter()
    for _ in range(iterations):
        resp = server.handle_request(raw)
        assert resp is not None
        assert "result" in resp
    elapsed = time.perf_counter() - start
    return elapsed


def benchmark_tools_call(server: MCPServer, iterations: int) -> float:
    """Benchmark tools/call requests. Returns seconds elapsed."""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "add", "arguments": {"a": 123, "b": 456}},
    }
    raw = json.dumps(req)
    start = time.perf_counter()
    for _ in range(iterations):
        resp = server.handle_request(raw)
        assert resp is not None
        assert "result" in resp
    elapsed = time.perf_counter() - start
    return elapsed


def benchmark_batch(server: MCPServer, iterations: int) -> float:
    """Benchmark batch requests (3 calls per batch). Returns seconds elapsed."""
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "add", "arguments": {"a": 1, "b": 2}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "echo", "arguments": {"message": "hi"}}},
    ]
    raw = json.dumps(batch)
    start = time.perf_counter()
    for _ in range(iterations):
        resp = server.handle_request(raw)
        assert resp is not None
        assert isinstance(resp, list)
        assert len(resp) == 3
    elapsed = time.perf_counter() - start
    return elapsed


def run_benchmark(iterations: int = 50_000, warmup: int = 1_000) -> None:
    """Run the full benchmark suite."""
    print(f"tiny-mcp Benchmark")
    print(f"Python: {sys.version}")
    print(f"Iterations: {iterations:,}")
    print(f"Warmup: {warmup:,}")
    print("-" * 50)

    server = create_server()

    # Warmup
    print("Warming up...")
    benchmark_tools_list(server, warmup)
    benchmark_tools_call(server, warmup)
    benchmark_batch(server, warmup // 3)
    print()

    # tools/list
    elapsed = benchmark_tools_list(server, iterations)
    rate = iterations / elapsed
    print(f"tools/list    : {rate:,.0f} req/sec  ({elapsed:.3f}s)")

    # tools/call
    elapsed = benchmark_tools_call(server, iterations)
    rate = iterations / elapsed
    print(f"tools/call    : {rate:,.0f} req/sec  ({elapsed:.3f}s)")

    # batch (3 requests per iteration)
    elapsed = benchmark_batch(server, iterations // 3)
    rate = (iterations // 3 * 3) / elapsed
    print(f"batch (3x)    : {rate:,.0f} req/sec  ({elapsed:.3f}s)")

    print("-" * 50)
    print("Benchmark complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="tiny-mcp throughput benchmark")
    parser.add_argument(
        "--iterations",
        type=int,
        default=50_000,
        help="Number of iterations per test (default: 50,000)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1_000,
        help="Number of warmup iterations (default: 1,000)",
    )
    args = parser.parse_args()
    run_benchmark(args.iterations, args.warmup)


if __name__ == "__main__":
    main()
