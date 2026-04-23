
import asyncio
import time
import httpx

from config import LLAMA_SERVER_URL, WORKER_COUNT, REQUEST_TIMEOUT
from metrics import (
    QUEUE_DEPTH,
    ACTIVE_WORKERS,
    INFERENCE_LATENCY,
    QUEUE_WAIT_TIME,
)

request_queue: asyncio.Queue = None


def init_queue(maxsize: int):
    global request_queue
    request_queue = asyncio.Queue(maxsize=maxsize)


async def worker(worker_id: int):
    """
    Each worker handles one request at a time.
    For streaming requests, it forwards chunks into a per-request chunk_queue.
    For non-streaming requests, it puts the full response into the chunk_queue
    as a single chunk, then signals done — keeping the interface consistent.
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        while True:
            queued_at, payload, chunk_queue = await request_queue.get()

            QUEUE_DEPTH.dec()
            ACTIVE_WORKERS.inc()

            queue_wait = time.time() - queued_at
            QUEUE_WAIT_TIME.observe(queue_wait)

            is_streaming = payload.get("stream", False)

            try:
                start = time.time()

                if is_streaming:
                  
                    async with client.stream(
                        "POST",
                        f"{LLAMA_SERVER_URL}/v1/chat/completions",
                        json=payload,
                    ) as response:
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                await chunk_queue.put(("chunk", line))
                  
                            if line == "data: [DONE]":
                                break

                else:
                   
                    # response = await client.post(
                    #     f"{LLAMA_SERVER_URL}/v1/chat/completions",
                    #     json=payload,
                    # )
                    # await chunk_queue.put(("response", response.json()))
                    # Non-streaming: get full response, wrap it like a single chunk
                    response = await client.post(
                        f"{LLAMA_SERVER_URL}/v1/chat/completions",
                        json=payload,
                    )
                    # print("DEBUG response status:", response.status_code)
                    # print("DEBUG response body:", response.text)
                    await chunk_queue.put(("response", response.json()))


                inference_time = time.time() - start
                INFERENCE_LATENCY.observe(inference_time)
                from controller import record_latency
                record_latency(inference_time)

            except Exception as e:
                
                await chunk_queue.put(("error", str(e)))

            finally:
                
                await chunk_queue.put(("done", None))
                ACTIVE_WORKERS.dec()
                request_queue.task_done()


async def start_workers():
    for i in range(WORKER_COUNT):
        asyncio.create_task(worker(i))