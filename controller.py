# controller.py
# Adaptive concurrency controller.
# Runs as a background task, watches p95 latency, and dynamically
# adjusts the number of active workers to stay near the target latency.

import asyncio
import time
from collections import deque

from config import WORKER_COUNT
from metrics import ACTIVE_WORKERS
import queue_worker

# --- Controller configuration ---

TARGET_LATENCY = 0.25       # target p95 latency in seconds
LATENCY_HEADROOM = 0.7      # only add workers when p95 is below 70% of target
WINDOW_SIZE = 20            # prevents oscillation at the boundary

MIN_WORKERS = 1              # never go below this
MAX_WORKERS = 12             # never go above this

ADJUSTMENT_INTERVAL = 5.0   # seconds between adjustments
                             # gives system time to stabilize after each change

WINDOW_SIZE = 50             # how many recent latency samples to keep
                             # p95 computed over this rolling window

# --- Shared state ---
# deque is a fixed-size ring buffer — oldest samples drop off automatically
latency_window: deque = deque(maxlen=WINDOW_SIZE)

# track currently running worker tasks so we can cancel them
worker_tasks: list = []


def record_latency(latency: float):
    """
    Called by queue_worker after every inference.
    Adds the latency sample to the rolling window.
    """
    latency_window.append(latency)


def compute_p95() -> float | None:
    """
    Computes p95 latency from the current window.
    Returns None if we don't have enough samples yet.
    """
    if len(latency_window) < 10:
        # not enough data to make a decision yet
        return None

    sorted_latencies = sorted(latency_window)
    index = int(len(sorted_latencies) * 0.95)
    return sorted_latencies[index]

async def add_worker():
    """Spawns one new worker task and tracks it."""
    from queue_worker import worker
    task = asyncio.create_task(worker(len(worker_tasks)))
    worker_tasks.append(task)


def remove_worker():
    """
    Cancels the most recently added worker task.
    The worker's finally block in queue_worker.py ensures
    it finishes its current request before the cancellation
    actually takes effect.
    """
    if worker_tasks:
        task = worker_tasks.pop()
        task.cancel()

async def controller_loop():
    """
    Background task that runs forever.
    Every ADJUSTMENT_INTERVAL seconds, checks p95 latency and
    adjusts worker count up or down by one.
    One step at a time — never jumps multiple workers at once.
    """
    # wait for the system to warm up before making any adjustments
    await asyncio.sleep(ADJUSTMENT_INTERVAL * 2)

    while True:
        await asyncio.sleep(ADJUSTMENT_INTERVAL)

        p95 = compute_p95()

        if p95 is None:
            # not enough samples yet, skip this round
            print("[controller] not enough samples yet, waiting...")
            continue

        current_count = len(worker_tasks)

        print(f"[controller] p95={p95:.3f}s workers={current_count}")

        if p95 > TARGET_LATENCY and current_count > MIN_WORKERS:
            # latency too high — reduce concurrency
            remove_worker()
            print(f"[controller] latency high, reducing to {current_count - 1} workers")

        elif p95 < TARGET_LATENCY * LATENCY_HEADROOM and current_count < MAX_WORKERS:
            # latency comfortably low — try adding a worker
            await add_worker()
            print(f"[controller] latency low, increasing to {current_count + 1} workers")

        else:
            print(f"[controller] latency nominal, holding at {current_count} workers")


async def start_controller(initial_worker_count: int):
    """
    Called at startup. Spawns the initial worker pool and
    starts the controller loop as a background task.
    Replaces start_workers() from queue_worker.py.
    """
    from queue_worker import worker

    for i in range(initial_worker_count):
        task = asyncio.create_task(worker(i))
        worker_tasks.append(task)

    asyncio.create_task(controller_loop())
    print(f"[controller] started with {initial_worker_count} workers, target p95={TARGET_LATENCY}s")

