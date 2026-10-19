"""Tiny async DAG runner: Kahn topological levels, parallel within a level."""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Node:
    name: str
    fn: Callable                      # fn(results: dict) -> any  (sync; runs in a worker thread)
    deps: list = field(default_factory=list)


class DagError(Exception):
    def __init__(self, node, error, timings=None):
        super().__init__(f"{node}: {error}")
        self.node, self.error, self.timings = node, error, timings or {}


def topo_levels(nodes):
    by_name = {n.name: n for n in nodes}
    for n in nodes:
        for d in n.deps:
            if d not in by_name:
                raise ValueError(f"unknown dependency '{d}' for node '{n.name}'")
    indeg = {n.name: len(n.deps) for n in nodes}
    children = {n.name: [] for n in nodes}
    for n in nodes:
        for d in n.deps:
            children[d].append(n.name)
    level = [name for name, k in indeg.items() if k == 0]
    levels, seen = [], 0
    while level:
        levels.append(sorted(level))
        seen += len(level)
        nxt = []
        for name in level:
            for c in children[name]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    nxt.append(c)
        level = nxt
    if seen != len(nodes):
        raise ValueError("cycle detected in DAG")
    return levels


async def run_dag(nodes, parallel=True):
    by_name = {n.name: n for n in nodes}
    levels = topo_levels(nodes)
    results, timings = {}, {}
    t0 = time.perf_counter()

    async def run_one(name):
        start = time.perf_counter() - t0
        err = None
        try:
            results[name] = await asyncio.to_thread(by_name[name].fn, results)
        except Exception as e:
            results[name] = None
            err = f"{type(e).__name__}: {e}"
        timings[name] = {"start": round(start, 3),
                         "end": round(time.perf_counter() - t0, 3),
                         "error": err}

    for level in levels:
        if parallel:
            await asyncio.gather(*(run_one(n) for n in level))
        else:
            for n in level:
                await run_one(n)
        failed = [n for n in level if timings[n]["error"]]
        if failed:
            raise DagError(failed[0], timings[failed[0]]["error"], timings)

    return {"results": results, "timings": timings, "levels": levels,
            "total_s": round(time.perf_counter() - t0, 3), "parallel": parallel}
