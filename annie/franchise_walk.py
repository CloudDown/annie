"""Parcours BFS franchise (MAL Jikan / AniList) — batch parallèle."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed


def drain_franchise_queue(
    *,
    queue: list[tuple[int, bool, str]],
    queued: set[int],
    seen: dict[int, object],
    max_nodes: int,
    pool: ThreadPoolExecutor | None,
    workers: int,
    is_cached: Callable[[int], bool],
    fetch: Callable[[int], dict],
    ingest: Callable[[dict, bool, str], None],
) -> None:
    """Vide la queue franchise jusqu'à max_nodes nœuds ingérés."""

    def _run_batch(
        batch: list[tuple[int, bool, str]], executor: ThreadPoolExecutor
    ) -> None:
        if not batch:
            return
        cached: list[tuple[dict, bool, str]] = []
        pending: list[tuple[int, bool, str]] = []
        for node_id, from_recap, via_relation in batch:
            if is_cached(node_id):
                try:
                    cached.append((fetch(node_id), from_recap, via_relation))
                except Exception:
                    pending.append((node_id, from_recap, via_relation))
            else:
                pending.append((node_id, from_recap, via_relation))

        for data, from_recap, via_relation in cached:
            ingest(data, from_recap, via_relation)

        if not pending:
            return

        futures: dict[Future[dict], tuple[int, bool, str]] = {
            executor.submit(fetch, node_id): (node_id, from_recap, via_relation)
            for node_id, from_recap, via_relation in pending
        }
        for future in as_completed(futures):
            _, from_recap, via_relation = futures[future]
            try:
                data = future.result()
            except Exception:
                continue
            ingest(data, from_recap, via_relation)

    def _drain(executor: ThreadPoolExecutor) -> None:
        while queue and len(seen) < max_nodes:
            batch: list[tuple[int, bool, str]] = []
            while queue and len(seen) + len(batch) < max_nodes:
                node_id, from_recap, via_relation = queue.pop(0)
                if node_id in seen:
                    continue
                batch.append((node_id, from_recap, via_relation))
            _run_batch(batch, executor)

    if pool is None:
        with ThreadPoolExecutor(max_workers=workers) as local:
            _drain(local)
    else:
        _drain(pool)
