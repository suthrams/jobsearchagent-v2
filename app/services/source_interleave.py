"""Source-fair round-robin interleave for discovered postings (ADR-102).

Discovery concatenates scraper results in append order (Adzuna first, then the
per-run ATS scrapers). Every downstream truncation - the discovery-service cap,
the discover_jobs node cap, and the score_jobs scoring cap - then keeps a prefix
of that list, so a later-appended source (e.g. Lever) is starved before it is
ever scored. Interleaving by source ONCE, before the first cap, makes all three
truncations source-fair with no source hierarchy and no new config.

The interleave is deterministic (no randomness) so runs stay reproducible and the
order-asserting tests are stable. It only reorders - it never adds or drops an
item, preserving never-lose-the-run.
"""
from __future__ import annotations

from operator import attrgetter
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")

_default_key = attrgetter("source")


def interleave_by_source(
    items: list[T], key: Callable[[T], object] = _default_key
) -> list[T]:
    """Return items round-robined across their source groups.

    Groups items by ``key(item)`` preserving first-seen group order AND
    within-group order, then emits one item per non-empty group per round until
    every group is drained:

        [a0, b0, c0, a1, b1, c1, a2, ...]

    Identity on empty or single-source inputs. Stable: the result is a pure
    function of the input order, so calling it twice yields the same list. The
    source group order is the order each source first appears in ``items`` (so a
    deterministic upstream scraper order yields a deterministic interleave).
    """
    if len(items) <= 1:
        return list(items)

    groups: dict[object, list[T]] = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)

    if len(groups) <= 1:
        # Single source - interleaving is a no-op; avoid the churn.
        return list(items)

    buckets: list[list[T]] = list(groups.values())
    result: list[T] = []
    # Round-robin: index i picks the i-th element from each bucket that has one.
    i = 0
    remaining = len(items)
    while remaining:
        for bucket in buckets:
            if i < len(bucket):
                result.append(bucket[i])
                remaining -= 1
        i += 1
    return result


def source_mix(items: Iterable[T], key: Callable[[T], object] = _default_key) -> dict:
    """Per-source counts (stringified source -> count), for discovery_stats."""
    counts: dict[str, int] = {}
    for item in items:
        k = key(item)
        name = getattr(k, "value", k)
        counts[str(name)] = counts.get(str(name), 0) + 1
    return counts
