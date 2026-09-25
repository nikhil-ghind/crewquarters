"""Benchmark a local model profile through the model gateway (run on the GB10 device).

Measures cold start (manual load until READY), warm first-token latency and decode
throughput (streaming), and host memory before load / while resident / after unload.
Writes a JSON report; copy the numbers into docs/benchmarks/gb10.md.

    python infra/scripts/benchmark_model.py --model local.general.small --runs 5 \
        --gateway http://127.0.0.1:8090 --token <internal service token>
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

import httpx

PROMPT = "Summarize the benefits of running AI models locally in five bullet points."


def wait_state(client: httpx.Client, model: str, wanted: str, timeout: float) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/internal/v1/models/{model}").json()
        if state["memoryState"] == wanted:
            return state
        if state["memoryState"] in ("LOAD_ERROR", "ERROR"):
            sys.exit(f"load failed: {state['error']}")
        time.sleep(1)
    sys.exit(f"timed out waiting for {wanted}")


def available(client: httpx.Client) -> int:
    return int(client.get("/internal/v1/memory").json()["availableBytes"] or 0)


def stream_once(client: httpx.Client, model: str) -> tuple[float, float, int]:
    body = {
        "profile": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "maxOutputTokens": 512,
        "stream": True,
        "holder": {"type": "chat", "id": "benchmark", "label": "Benchmark"},
    }
    start = time.perf_counter()
    first = None
    tokens = 0
    with client.stream("POST", "/internal/v1/llm/chat", json=body, timeout=600) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event["type"] == "delta" and first is None:
                first = time.perf_counter()
            if event["type"] == "done":
                tokens = event["response"]["usage"]["outputTokens"]
            if event["type"] == "error":
                sys.exit(f"inference failed: {event['error']}")
    end = time.perf_counter()
    first = first or end
    return (first - start) * 1000, tokens / max(end - first, 1e-6), tokens


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="local.general.small")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--gateway", default="http://127.0.0.1:8090")
    parser.add_argument("--token", required=True)
    parser.add_argument("--out", default="benchmark.json")
    args = parser.parse_args()
    client = httpx.Client(
        base_url=args.gateway, headers={"Authorization": f"Bearer {args.token}"}, timeout=60
    )

    before = available(client)
    started = time.perf_counter()
    client.post(f"/internal/v1/models/{args.model}/load").raise_for_status()
    wait_state(client, args.model, "READY", 1800)
    cold = time.perf_counter() - started
    resident = available(client)
    stream_once(client, args.model)  # warm-up
    samples = [stream_once(client, args.model) for _ in range(args.runs)]
    client.delete("/internal/v1/leases/holders/chat/benchmark")
    client.post(f"/internal/v1/models/{args.model}/unload", json={"force": True}).raise_for_status()
    wait_state(client, args.model, "NOT_LOADED", 600)
    time.sleep(5)
    after = available(client)
    report = {
        "model": args.model,
        "coldStartSeconds": round(cold, 1),
        "firstTokenMsMedian": round(statistics.median(s[0] for s in samples), 1),
        "decodeTokensPerSecondMedian": round(statistics.median(s[1] for s in samples), 1),
        "outputTokensMedian": statistics.median(s[2] for s in samples),
        "availableGiB": {
            "beforeLoad": round(before / 2**30, 1),
            "resident": round(resident / 2**30, 1),
            "afterUnload": round(after / 2**30, 1),
        },
        "runs": args.runs,
    }
    with open(args.out, "w") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
