# main.py
import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from config import GATEWAY_HOST, GATEWAY_PORT, QUEUE_MAXSIZE, WORKER_COUNT
from metrics import REQUESTS_TOTAL, TOTAL_REQUEST_TIME, QUEUE_DEPTH
import queue_worker

import controller

@asynccontextmanager
async def lifespan(app: FastAPI):
    queue_worker.init_queue(maxsize=QUEUE_MAXSIZE)
    await controller.start_controller(initial_worker_count=WORKER_COUNT)
    print(f"Gateway started — {QUEUE_MAXSIZE} queue slots, workers ready")
    yield
    print("Gateway shutting down")


app = FastAPI(lifespan=lifespan)


async def stream_chunks(chunk_queue: asyncio.Queue, start_time: float):
    try:
        while True:
            tag, data = await chunk_queue.get()

            if tag == "chunk":
                yield f"{data}\n\n"
                if "[DONE]" in data:
                    REQUESTS_TOTAL.labels(status="success").inc()
                    TOTAL_REQUEST_TIME.observe(time.time() - start_time)
                    break

            elif tag == "error":
                yield f"data: {{\"error\": \"{data}\"}}\n\n"
                REQUESTS_TOTAL.labels(status="error").inc()
                break

            elif tag == "done":
                break

    except asyncio.CancelledError:
        pass


async def handle_non_streaming(chunk_queue: asyncio.Queue, start_time: float):
    try:
        while True:
            tag, data = await chunk_queue.get()

            if tag == "response":
                REQUESTS_TOTAL.labels(status="success").inc()
                TOTAL_REQUEST_TIME.observe(time.time() - start_time)
                return JSONResponse(content=data)

            elif tag == "error":
                REQUESTS_TOTAL.labels(status="error").inc()
                raise HTTPException(status_code=502, detail=f"Inference error: {data}")

            elif tag == "done":
                raise HTTPException(status_code=502, detail="Worker finished without response")

    except httpx.TimeoutException:
        REQUESTS_TOTAL.labels(status="error").inc()
        raise HTTPException(status_code=504, detail="llama.cpp did not respond in time")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    start_time = time.time()

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    is_streaming = payload.get("stream", False)

    if queue_worker.request_queue.full():
        REQUESTS_TOTAL.labels(status="rejected").inc()
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Gateway overloaded",
                "message": "Request queue is full. Try again shortly."
            }
        )

    chunk_queue = asyncio.Queue()
    await queue_worker.request_queue.put((time.time(), payload, chunk_queue))
    QUEUE_DEPTH.inc()

    if is_streaming:
        return StreamingResponse(
            stream_chunks(chunk_queue, start_time),
            media_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
            }
        )
    else:
        return await handle_non_streaming(chunk_queue, start_time)


@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "queue_depth": queue_worker.request_queue.qsize(),
        "queue_max": QUEUE_MAXSIZE,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=GATEWAY_HOST, port=GATEWAY_PORT, reload=False)