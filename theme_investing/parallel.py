"""Bounded I/O concurrency with deterministic caller-owned result application."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Callable, Iterable, Iterator
from threading import Event, Lock
from typing import TypeVar

from llm_errors import LLMWorkflowAbort

_T = TypeVar("_T")
_R = TypeVar("_R")


def ordered_map(
    fn: Callable[[_T], _R], items: Iterable[_T], *, workers: int,
) -> Iterator[_R]:
    """Execute independent work concurrently, yielding in original input order.

    Workers return values; the caller applies shared state and budget decisions.
    One worker deliberately uses the synchronous path for serial comparisons and
    compatibility with caller-owned clients that cannot be used across threads.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if workers == 1:
        yield from map(fn, items)
        return
    aborted = Event()
    abort_lock = Lock()
    abort_error: LLMWorkflowAbort | None = None

    def raise_if_aborted() -> None:
        if aborted.is_set():
            assert abort_error is not None
            raise abort_error

    def invoke(item: _T) -> _R:
        nonlocal abort_error
        # A worker can finish before the coordinator notices its failure. Stop
        # queued work here as well as cancelling its Future during cleanup.
        raise_if_aborted()
        try:
            return fn(item)
        except LLMWorkflowAbort as exc:
            with abort_lock:
                if not aborted.is_set():
                    abort_error = exc
                    aborted.set()
            raise

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="etf")
    fatal_exit = False
    try:
        futures = {
            pool.submit(invoke, item): index for index, item in enumerate(items)
        }
        completed = {}
        next_index = 0
        for future in as_completed(futures):
            # Observe fatal completion immediately, even if an earlier input is
            # still running. Ordinary results/errors retain input ordering.
            raise_if_aborted()
            completed[futures[future]] = future
            while next_index in completed:
                raise_if_aborted()
                yield completed.pop(next_index).result()
                next_index += 1
    except LLMWorkflowAbort:
        fatal_exit = True
        raise
    finally:
        fatal_exit = fatal_exit or aborted.is_set()
        # Running requests cooperate through the LLM client's cancellation
        # event; their socket timeout still bounds an in-flight network read.
        pool.shutdown(wait=not fatal_exit, cancel_futures=fatal_exit)
