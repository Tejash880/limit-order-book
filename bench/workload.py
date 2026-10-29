"""Deterministic seeded workload generator for matching engine benchmarks."""

from __future__ import annotations
import random
from typing import List, Tuple, Any
from lob.models import Side, OrderType


def generate_workload(
    n_ops: int = 1_000_000,
    seed: int = 42,
    initial_mid: int = 1000,
) -> List[Tuple[Any, ...]]:
    """Pre-generate a deterministic workload of operations.

    Distribution:
    - ~60% LIMIT orders (clustered around moving mid-price)
    - ~30% CANCEL operations (targeting recently placed active orders)
    - ~10% MARKET orders (consuming liquidity)

    Returns:
        List of pre-constructed tuples to eliminate PRNG overhead during timing.
    """
    rng = random.Random(seed)
    workload: List[Tuple[Any, ...]] = []
    active_ids: List[str] = []

    mid = initial_mid
    order_seq = 0

    for _ in range(n_ops):
        # Brownian random walk for mid price
        if rng.random() < 0.05:
            mid += rng.choice([-1, 1])
            mid = max(100, min(mid, 10_000))

        p = rng.random()

        if p < 0.60:
            order_seq += 1
            oid = f"o{order_seq}"
            side = Side.BUY if rng.random() < 0.50 else Side.SELL
            # Offset clustered around mid price (Gaussian-like discrete spread)
            offset = rng.randint(-15, 15)
            price = max(1, mid + offset)
            qty = rng.randint(1, 100)
            workload.append(("LIMIT", oid, side, qty, price))
            active_ids.append(oid)

        elif p < 0.90:
            if active_ids:
                # Cancel an active order (preferring recent ones)
                back_idx = rng.randint(1, min(len(active_ids), 500))
                oid = active_ids[-back_idx]
                workload.append(("CANCEL", oid))
            else:
                order_seq += 1
                oid = f"o{order_seq}"
                side = Side.BUY if rng.random() < 0.50 else Side.SELL
                price = max(1, mid + rng.randint(-5, 5))
                qty = rng.randint(1, 100)
                workload.append(("LIMIT", oid, side, qty, price))
                active_ids.append(oid)

        else:
            order_seq += 1
            oid = f"m{order_seq}"
            side = Side.BUY if rng.random() < 0.50 else Side.SELL
            qty = rng.randint(1, 50)
            workload.append(("MARKET", oid, side, qty))

    return workload
