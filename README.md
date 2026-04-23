# LLM Inference Gateway

An OpenAI-compatible inference gateway that sits in front of a `llama.cpp` backend and adds everything a production serving layer needs — request queuing, backpressure, streaming, adaptive concurrency control, and observability.

Drop-in compatible with any OpenAI SDK client.

---

## Architecture

```
Client (curl / OpenAI SDK)
            │
            ▼
┌───────────────────────┐
│    FastAPI Gateway    │
│                       │
│   Bounded Queue       │
│        │              │
│   Worker Pool         │
│        │              │
│   Concurrency         │
│   Controller          │
└──────────┬────────────┘
           │
           ▼
     llama.cpp server
```

---

## Features

- **OpenAI-compatible** — `/v1/chat/completions` works with any OpenAI SDK, no changes needed
- **Async request queue** — bounded producer/consumer queue decouples ingestion from inference, absorbs burst traffic without hammering the backend
- **Backpressure** — returns `HTTP 429` when the queue is full instead of letting the system degrade silently
- **SSE token streaming** — tokens stream to the client in real time as they're generated, matching the OpenAI streaming protocol exactly
- **Adaptive concurrency** — feedback loop watches p95 latency over a time-decayed sliding window and resizes the worker pool at runtime. Scales up when there's headroom, scales down when latency breaches the threshold. Same idea as TCP congestion control
- **Prometheus metrics** — `/metrics` exposes latency histograms, queue depth, active workers, and request counters ready to scrape

---

## How It Works

**Queue + workers**
Every request enters a bounded `asyncio.Queue`. A worker pool drains it at a controlled concurrency level — this is what stops the backend from getting overwhelmed. If the queue fills up, new requests get a `429` immediately.

**Adaptive concurrency controller**
A background task runs every few seconds, computes p95 latency over a time-decayed window (stale samples auto-expire), and adjusts worker count one step at a time. The headroom threshold prevents oscillation — it only adds workers when latency is comfortably below target, not right at the edge.

**Streaming**
Each request gets its own private `asyncio.Queue`. For streaming requests, the worker pushes SSE chunks into it as they arrive from llama.cpp. The endpoint reads from it via an async generator and forwards chunks to the client immediately. `asyncio.CancelledError` handles client disconnects cleanly.

---

## Setup

```bash
# install llama.cpp
brew install llama.cpp

# download a model
huggingface-cli download bartowski/Llama-3.2-3B-Instruct-GGUF \
  Llama-3.2-3B-Instruct-Q4_K_M.gguf --local-dir ~/models

# start llama.cpp
llama-server --model ~/models/Llama-3.2-3B-Instruct-Q4_K_M.gguf \
  --port 8080 --n-gpu-layers 99 --ctx-size 2048

# install dependencies
pip install -r requirements.txt

# start the gateway
python main.py
```

---

## Usage

```bash
# non-streaming
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"llama","messages":[{"role":"user","content":"hello"}],"max_tokens":100}'

# streaming
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"llama","messages":[{"role":"user","content":"hello"}],"max_tokens":100,"stream":true}'

# health
curl http://localhost:8000/health

# metrics
curl http://localhost:8000/metrics
```

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `WORKER_COUNT` | `4` | Initial worker pool size |
| `QUEUE_MAXSIZE` | `100` | Queue capacity before 429 |
| `TARGET_LATENCY` | `0.25s` | p95 latency target |
| `LATENCY_HEADROOM` | `0.7` | Scale up threshold (target × headroom) |
| `MIN_WORKERS` | `1` | Worker pool floor |
| `MAX_WORKERS` | `12` | Worker pool ceiling |
| `ADJUSTMENT_INTERVAL` | `5s` | Controller adjustment frequency |
| `WINDOW_MAX_AGE` | `30s` | Latency sample TTL |

---

## Stack

Python · FastAPI · asyncio · llama.cpp · Prometheus