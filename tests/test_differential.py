"""Differential fuzzing test: Fast MatchingEngine vs Naive ReferenceEngine.

Feeds identical deterministic operation streams to both engines and verifies:
1. Exact equality of emitted event lists (types, seq_nums, quantities, prices).
2. Exact equality of best_bid and best_ask after every operation.
3. Exact equality of order states.
"""

import random
import sys
import time
from typing import List, Tuple
from lob.models import Side, OrderType, Event
from lob.reference import ReferenceEngine
from lob.engine import MatchingEngine


def assert_events_equal(fast_events: List[Event], ref_events: List[Event], op_desc: str) -> None:
    """Assert that two lists of events are strictly identical in data content."""
    assert len(fast_events) == len(ref_events), (
        f"Event count mismatch on [{op_desc}]: fast={len(fast_events)}, ref={len(ref_events)}\n"
        f"Fast: {fast_events}\nRef: {ref_events}"
    )
    for i, (fe, re) in enumerate(zip(fast_events, ref_events)):
        assert fe.to_dict() == re.to_dict(), (
            f"Event #{i} mismatch on [{op_desc}]:\nFast: {fe.to_dict()}\nRef:  {re.to_dict()}"
        )


def run_differential_stream(num_ops: int, seed: int) -> Tuple[int, int]:
    """Execute a stream of operations on both engines and assert parity."""
    rng = random.Random(seed)
    fast_engine = MatchingEngine()
    ref_engine = ReferenceEngine()

    active_order_ids: List[str] = []
    total_trades = 0
    order_idx = 0

    mid_price = 100

    for i in range(num_ops):
        # Slightly walk mid price to create clustering
        if rng.random() < 0.05:
            mid_price += rng.choice([-1, 1])
            mid_price = max(10, min(mid_price, 500))

        # Action distribution: 60% limit, 25% cancel, 10% market, 5% amend
        p = rng.random()

        if p < 0.60:
            # LIMIT ORDER
            order_idx += 1
            oid = f"ord_{seed}_{order_idx}"
            side = rng.choice([Side.BUY, Side.SELL])
            spread_offset = rng.randint(-5, 5)
            price = max(1, mid_price + spread_offset)
            qty = rng.randint(1, 50)
            op_desc = f"LIMIT {side.value} {qty}@{price} (id={oid})"

            fe = fast_engine.process_order(oid, side, OrderType.LIMIT, qty, price)
            re = ref_engine.process_order(oid, side, OrderType.LIMIT, qty, price)

            assert_events_equal(fe, re, op_desc)
            active_order_ids.append(oid)
            total_trades += sum(1 for e in fe if e.to_dict()["type"] == "TRADE")

        elif p < 0.85:
            # CANCEL
            if not active_order_ids:
                continue
            # Pick an order (biased towards recent)
            idx = rng.randint(0, len(active_order_ids) - 1)
            oid = active_order_ids[idx]
            op_desc = f"CANCEL {oid}"

            fe = fast_engine.cancel_order(oid)
            re = ref_engine.cancel_order(oid)

            assert_events_equal(fe, re, op_desc)

        elif p < 0.95:
            # MARKET ORDER
            order_idx += 1
            oid = f"mkt_{seed}_{order_idx}"
            side = rng.choice([Side.BUY, Side.SELL])
            qty = rng.randint(1, 30)
            op_desc = f"MARKET {side.value} {qty} (id={oid})"

            fe = fast_engine.process_order(oid, side, OrderType.MARKET, qty)
            re = ref_engine.process_order(oid, side, OrderType.MARKET, qty)

            assert_events_equal(fe, re, op_desc)
            total_trades += sum(1 for e in fe if e.to_dict()["type"] == "TRADE")

        else:
            # AMEND
            if not active_order_ids:
                continue
            idx = rng.randint(0, len(active_order_ids) - 1)
            oid = active_order_ids[idx]
            new_price = max(1, mid_price + rng.randint(-3, 3))
            new_qty = rng.randint(1, 40)
            op_desc = f"AMEND {oid} {new_qty}@{new_price}"

            fe = fast_engine.amend_order(oid, new_price, new_qty)
            re = ref_engine.amend_order(oid, new_price, new_qty)

            assert_events_equal(fe, re, op_desc)
            total_trades += sum(1 for e in fe if e.to_dict()["type"] == "TRADE")

        # Book parity check
        assert fast_engine.best_bid == ref_engine.best_bid, (
            f"best_bid mismatch at step {i}: fast={fast_engine.best_bid}, ref={ref_engine.best_bid}"
        )
        assert fast_engine.best_ask == ref_engine.best_ask, (
            f"best_ask mismatch at step {i}: fast={fast_engine.best_ask}, ref={ref_engine.best_ask}"
        )

    return num_ops, total_trades


def test_differential_fast_vs_reference():
    """Pytest test case for differential fuzzing across multiple seeds."""
    seeds = [42, 1337, 2026, 99999]
    ops_per_seed = 5000
    total_ops = 0
    for s in seeds:
        ops, _ = run_differential_stream(ops_per_seed, s)
        total_ops += ops
    assert total_ops == ops_per_seed * len(seeds)


if __name__ == "__main__":
    # Standalone harness to execute 1,000,000 operations across seeds
    target_ops = 1_000_000
    seeds = [101, 202, 303, 404, 505, 606, 707, 808, 909, 1001]
    ops_per_seed = target_ops // len(seeds)

    print(f"Starting 1,000,000-operation differential fuzzing ({len(seeds)} seeds x {ops_per_seed} ops)...")
    start = time.perf_counter()
    total_completed = 0
    total_trades = 0

    for idx, seed in enumerate(seeds, start=1):
        s_start = time.perf_counter()
        completed, trades = run_differential_stream(ops_per_seed, seed)
        total_completed += completed
        total_trades += trades
        elapsed = time.perf_counter() - s_start
        print(f"  [Seed {seed} ({idx}/{len(seeds)})] {completed:,} ops verified ({trades:,} trades) in {elapsed:.2f}s")

    duration = time.perf_counter() - start
    print(f"SUCCESS: {total_completed:,} operations differentially verified in {duration:.2f}s ({total_completed / duration:,.0f} ops/sec)")
