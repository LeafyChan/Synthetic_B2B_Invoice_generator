"""
pipeline/tier_planner.py
=========================
Resolves the tier (clean/degraded/heavy) for every doc_index in a run.

Two modes
---------
1. DEFAULT (no --tier-counts given): every doc_index's tier comes from
   degradation.assign_tier(doc_index) exactly as before — unchanged
   behaviour, fully backward compatible. Tier distribution naturally
   lands near the global 50/30/20 split for any reasonably large count.

2. EXACT COUNTS (--tier-counts clean=3,degraded=3,heavy=4 with --count 10):
   the requested counts are honoured exactly. We don't touch
   degradation.assign_tier() (still used to build the candidate ordering so
   the assignment stays deterministic/reproducible per the same prime-salt
   scheme), we just construct an explicit {doc_index: tier} map sized to
   match the requested counts, deterministically, instead of letting the
   probabilistic split decide.

Discrepancy
-----------
Independent of tier — discrepancy assignment (data_models._assign_discrepancy)
is left as-is; every discrepant doc-pair still also has a tier (and thus a
visual degradation profile) and is additionally copied into discrepant/.
This module does not control discrepancy ratio; that knob lives in
data_models.DISCREPANCY_RATE. If you want an exact discrepant count too
(e.g. "4 of these 10 must be discrepant"), see plan_discrepancy_indices()
below — same exact-count pattern, separate axis.

Usage
-----
    from pipeline.tier_planner import resolve_tier_plan, parse_tier_counts

    counts = parse_tier_counts("clean=3,degraded=3,heavy=4")  # or None
    tier_for_index = resolve_tier_plan(indices, counts)        # dict[int, str]
    ...
    tier = tier_for_index[idx]   # pass explicitly into generate_document_pair /
                                  # degrade_image / degrade_pdf instead of None
"""

from __future__ import annotations

import logging
from typing import Optional

from pipeline.degradation import assign_tier, _TIER_ORDER

log = logging.getLogger("tier_planner")

VALID_TIERS = set(_TIER_ORDER)  # {"clean", "degraded", "heavy"}


def parse_tier_counts(raw: Optional[str]) -> Optional[dict[str, int]]:
    """
    Parse a CLI string like "clean=3,degraded=3,heavy=4" into
    {"clean": 3, "degraded": 3, "heavy": 4}.

    Returns None if raw is None/empty (caller should fall back to default
    probabilistic assign_tier() behaviour).

    Raises ValueError on malformed input or unknown tier names, so bad
    input fails fast at CLI-parse time rather than mid-run.
    """
    if not raw:
        return None
    counts: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(
                f"Invalid --tier-counts segment '{part}'. Expected format: "
                f"clean=3,degraded=3,heavy=4"
            )
        tier, n_str = part.split("=", 1)
        tier = tier.strip().lower()
        if tier not in VALID_TIERS:
            raise ValueError(
                f"Unknown tier '{tier}' in --tier-counts. Valid tiers: "
                f"{sorted(VALID_TIERS)}"
            )
        try:
            n = int(n_str.strip())
        except ValueError:
            raise ValueError(f"Invalid count '{n_str}' for tier '{tier}' in --tier-counts")
        if n < 0:
            raise ValueError(f"Tier count for '{tier}' cannot be negative")
        counts[tier] = n
    return counts


def resolve_tier_plan(
    indices: list[int],
    tier_counts: Optional[dict[str, int]] = None,
) -> dict[int, str]:
    """
    Return {doc_index: tier} for every index in `indices`.

    indices     : the exact list of doc_index values this run will generate
                  (e.g. range(start_index, start_index + count))
    tier_counts : parsed output of parse_tier_counts(), or None.

    Behaviour
    ---------
    tier_counts is None
        → tier_for_index[idx] = assign_tier(idx) for every idx, i.e.
          IDENTICAL to current default behaviour. Nothing changes for
          existing callers/runs that don't pass --tier-counts.

    tier_counts given
        → sum(tier_counts.values()) MUST equal len(indices), or this raises
          ValueError immediately (fail fast — silently generating a
          different total than requested would be worse than refusing).
        → Indices are sorted, then bucketed deterministically: we compute
          each index's "natural" tier via assign_tier(idx) first (so the
          assignment still uses the same seeded RNG stream as default mode
          and is reproducible run-to-run), then greedily satisfy each
          tier's exact quota using indices that were ALREADY naturally
          assigned to that tier where possible, and only reassign
          (overflow/underflow) indices when a tier's natural count doesn't
          match its requested quota. This keeps the result as close as
          possible to "what would have happened by default" while still
          hitting the exact requested numbers — re-running with the same
          indices + same counts always produces the same plan.
    """
    if tier_counts is None:
        return {idx: assign_tier(idx) for idx in indices}

    requested_total = sum(tier_counts.values())
    if requested_total != len(indices):
        raise ValueError(
            f"--tier-counts totals {requested_total} but --count is "
            f"{len(indices)}. These must match exactly — e.g. for "
            f"--count 10, tier-counts must sum to 10 "
            f"(got {tier_counts})."
        )

    # Ensure every valid tier has an explicit (possibly zero) quota.
    quotas = {t: tier_counts.get(t, 0) for t in _TIER_ORDER}

    sorted_indices = sorted(indices)

    # Natural bucket: what assign_tier() would have said for each index,
    # using the SAME deterministic RNG stream as default mode.
    natural: dict[str, list[int]] = {t: [] for t in _TIER_ORDER}
    for idx in sorted_indices:
        natural[assign_tier(idx)].append(idx)

    plan: dict[int, str] = {}
    leftover_pool: list[int] = []  # indices not yet placed, in order

    # Pass 1: for each tier, take as many of its own "natural" indices as
    # its quota allows (deterministic — natural[] preserves sorted order).
    for tier in _TIER_ORDER:
        take = min(quotas[tier], len(natural[tier]))
        chosen = natural[tier][:take]
        for idx in chosen:
            plan[idx] = tier
        # whatever wasn't chosen from this tier's natural bucket goes into
        # the leftover pool to be redistributed in pass 2
        leftover_pool.extend(natural[tier][take:])
        quotas[tier] -= take

    leftover_pool.sort()

    # Pass 2: fill remaining quotas from the leftover pool, in deterministic
    # (sorted index) order, tier by tier in _TIER_ORDER so re-runs are stable.
    pool_iter = iter(leftover_pool)
    for tier in _TIER_ORDER:
        remaining = quotas[tier]
        for _ in range(remaining):
            idx = next(pool_iter)
            plan[idx] = tier

    # Sanity check — every index placed exactly once.
    unplaced = set(sorted_indices) - set(plan.keys())
    if unplaced:
        raise RuntimeError(
            f"tier_planner internal error: {len(unplaced)} indices were not "
            f"assigned a tier ({sorted(unplaced)[:10]}...). This should be "
            f"unreachable — please report this as a bug."
        )

    actual_counts = {t: sum(1 for v in plan.values() if v == t) for t in _TIER_ORDER}
    log.info(f"Exact tier plan resolved: requested={tier_counts} actual={actual_counts}")

    return plan


def plan_discrepancy_indices(
    indices: list[int],
    discrepant_count: Optional[int] = None,
) -> Optional[set[int]]:
    """
    Optional exact-count control for discrepancy, mirroring resolve_tier_plan.

    Returns None if discrepant_count is None (caller should fall back to
    data_models._assign_discrepancy(idx)'s default ~15% probabilistic
    behaviour — unchanged).

    Returns an explicit set of doc_indices that MUST be discrepant if
    discrepant_count is given, chosen deterministically (preferring indices
    that data_models._assign_discrepancy() would have picked naturally,
    same redistribute-on-mismatch pattern as resolve_tier_plan above) so
    re-runs with the same inputs are reproducible.

    NOTE: this is provided for symmetry/completeness (you mentioned wanting
    exact discrepant counts too) but wiring it into data_models.py requires
    generate_document_pair() to accept an optional `force_discrepancy: bool`
    param instead of always calling _assign_discrepancy(idx) internally —
    see PATCH_data_models.md for the exact change needed if you want this
    enabled; it's not wired into assembler.py in the current patch set
    since you didn't specify required discrepant counts, only tier counts.
    """
    if discrepant_count is None:
        return None
    if discrepant_count < 0 or discrepant_count > len(indices):
        raise ValueError(
            f"--discrepant-count {discrepant_count} must be between 0 and "
            f"{len(indices)} (the total doc count)"
        )

    from pipeline.data_models import _assign_discrepancy

    sorted_indices = sorted(indices)
    natural_yes = [i for i in sorted_indices if _assign_discrepancy(i)]
    natural_no = [i for i in sorted_indices if not _assign_discrepancy(i)]

    if discrepant_count <= len(natural_yes):
        return set(natural_yes[:discrepant_count])
    # Need more discrepant docs than naturally assigned — pull additional
    # ones from natural_no, in sorted order, deterministically.
    extra_needed = discrepant_count - len(natural_yes)
    return set(natural_yes) | set(natural_no[:extra_needed])