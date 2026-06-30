"""
pipeline/timing.py
====================
Lightweight per-step timing instrumentation, shared across the pipeline.

Why a separate module
-----------------------
Every stage (HTML render, PNG screenshot, PDF render, degradation,
handwriting overlay, DB write) currently has zero timing visibility — when
the pipeline is slow, there's no log line telling you WHICH step is the
bottleneck, only an aggregate docs/s rate in assembler.py's progress log.
This module gives every call site a one-line way to record "this step took
X seconds" and accumulates per-step stats per worker process, periodically
logging a breakdown (and a final summary at process exit) so you can see
e.g. "render_html_to_pdf is 70% of wall time" without attaching a profiler.

Usage
-----
    from pipeline.timing import timed, log_timing_summary

    async with timed("render_html_to_png"):
        png = await render_html_to_png(html)

    # or as a sync context manager:
    with timed("degrade_image"):
        out = degrade_image(raw, idx, tier=tier)

    # periodically (assembler.py's progress log already fires every
    # PROGRESS_INTERVAL docs — call this right alongside it):
    log_timing_summary()

Design notes
------------
- Per-process (module-level dict), NOT cross-process — each worker in
  assembler.py's ProcessPoolExecutor gets its own independent timing table,
  which is what you want here: it tells you whether e.g. PDF rendering is
  slow on every worker uniformly, or just one worker hitting contention.
- Cheap: time.perf_counter() before/after, a dict increment. No external
  deps, no overhead worth worrying about at 20,000-doc scale.
- `timed()` works as BOTH a sync and an async context manager (it doesn't
  need to know which — entering/exiting just record perf_counter(), and
  Python's `async with` on a plain context manager that also implements
  __aenter__/__aexit__ works fine; see _Timed below).
- On exception, the step is still recorded (in a "_failed" bucket per step
  name) rather than silently dropped, so a step that's slow because it's
  erroring/retrying is visible in the timing summary, not just in the
  separate failure count.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import AbstractContextManager
from typing import Optional

log = logging.getLogger("timing")

# Per-process accumulators: {step_name: [total_seconds, call_count]}
_STATS: dict[str, list] = defaultdict(lambda: [0.0, 0])
_FAILED_STATS: dict[str, int] = defaultdict(int)


class _Timed(AbstractContextManager):
    """Context manager usable as both `with timed(...)` and `async with timed(...)`."""

    __slots__ = ("name", "_t0")

    def __init__(self, name: str):
        self.name = name
        self._t0 = 0.0

    # sync
    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.perf_counter() - self._t0
        if exc_type is None:
            _STATS[self.name][0] += elapsed
            _STATS[self.name][1] += 1
        else:
            _FAILED_STATS[self.name] += 1
            # still record the time spent before it failed — a step that's
            # slow AND failing is exactly what you want visible
            _STATS[self.name][0] += elapsed
            _STATS[self.name][1] += 1
        return False  # never swallow exceptions

    # async
    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb):
        return self.__exit__(exc_type, exc, tb)


def timed(step_name: str) -> _Timed:
    """
    Time a block of code under `step_name`. Works for both:
        with timed("degrade_image"):
            ...
    and:
        async with timed("render_html_to_png"):
            ...
    """
    return _Timed(step_name)


def record(step_name: str, elapsed_seconds: float, failed: bool = False) -> None:
    """Manually record a duration for a step (when a context manager doesn't fit)."""
    _STATS[step_name][0] += elapsed_seconds
    _STATS[step_name][1] += 1
    if failed:
        _FAILED_STATS[step_name] += 1


def get_timing_snapshot() -> dict:
    """
    Return the current per-process timing table as a plain dict:
        {
          "render_html_to_pdf": {"total_s": 42.1, "calls": 60, "avg_ms": 701.7, "failed": 0},
          ...
        }
    Sorted by total_s descending so the biggest bottleneck is first.
    """
    snapshot = {}
    for name, (total, count) in _STATS.items():
        snapshot[name] = {
            "total_s": round(total, 3),
            "calls": count,
            "avg_ms": round((total / count) * 1000, 1) if count else 0.0,
            "failed": _FAILED_STATS.get(name, 0),
        }
    return dict(sorted(snapshot.items(), key=lambda kv: kv[1]["total_s"], reverse=True))


def log_timing_summary(prefix: str = "") -> None:
    """
    Log a one-block breakdown of every timed step so far in this process,
    biggest time-consumer first. Call this periodically (e.g. alongside
    assembler.py's existing PROGRESS_INTERVAL log) and once more at the end
    of each worker's run_shard().
    """
    snapshot = get_timing_snapshot()
    if not snapshot:
        return
    total_all = sum(v["total_s"] for v in snapshot.values())
    lines = [f"{prefix}Timing breakdown (total tracked time: {total_all:.1f}s):"]
    for name, stats in snapshot.items():
        pct = (stats["total_s"] / total_all * 100) if total_all else 0
        fail_note = f", {stats['failed']} failed" if stats["failed"] else ""
        lines.append(
            f"    {name:<28} {stats['total_s']:>8.1f}s  ({pct:5.1f}%)  "
            f"{stats['calls']:>5} calls  avg {stats['avg_ms']:>7.1f}ms{fail_note}"
        )
    log.info("\n".join(lines))


def reset_timing() -> None:
    """Clear all accumulated stats (mainly useful in tests)."""
    _STATS.clear()
    _FAILED_STATS.clear()