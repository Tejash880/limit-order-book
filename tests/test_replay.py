"""Tests for event logging, serialization, and deterministic replay."""

import random
import tempfile
from pathlib import Path
from lob.engine import MatchingEngine
from lob.eventlog import EventLogger, EventReplayer, hash_events
from lob.models import Side, OrderType


def test_event_logging_and_replay_determinism():
    """Verify that replaying a JSONL command stream reproduces the identical event hash and state."""
    rng = random.Random(2026)
    engine1 = MatchingEngine()
    events1 = []

    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "engine_input.jsonl"

        with EventLogger(log_path) as logger:
            active_ids = []
            order_idx = 0

            # Execute 1,000 mixed operations
            for _ in range(1000):
                p = rng.random()
                if p < 0.60:
                    order_idx += 1
                    oid = f"ord_{order_idx}"
                    side = rng.choice([Side.BUY, Side.SELL])
                    price = rng.randint(90, 110)
                    qty = rng.randint(1, 50)
                    cmd = {
                        "cmd": "NEW_ORDER",
                        "order_id": oid,
                        "side": side.value,
                        "order_type": "LIMIT",
                        "quantity": qty,
                        "price": price,
                    }
                    logger.log_command(cmd)
                    evs = engine1.process_order(oid, side, OrderType.LIMIT, qty, price)
                    events1.extend(evs)
                    active_ids.append(oid)

                elif p < 0.80 and active_ids:
                    oid = rng.choice(active_ids)
                    cmd = {"cmd": "CANCEL", "order_id": oid}
                    logger.log_command(cmd)
                    evs = engine1.cancel_order(oid)
                    events1.extend(evs)

                elif p < 0.90:
                    order_idx += 1
                    oid = f"mkt_{order_idx}"
                    side = rng.choice([Side.BUY, Side.SELL])
                    qty = rng.randint(1, 30)
                    cmd = {
                        "cmd": "NEW_ORDER",
                        "order_id": oid,
                        "side": side.value,
                        "order_type": "MARKET",
                        "quantity": qty,
                    }
                    logger.log_command(cmd)
                    evs = engine1.process_order(oid, side, OrderType.MARKET, qty)
                    events1.extend(evs)

                elif active_ids:
                    oid = rng.choice(active_ids)
                    new_price = rng.randint(90, 110)
                    new_qty = rng.randint(1, 40)
                    cmd = {
                        "cmd": "AMEND",
                        "order_id": oid,
                        "new_price": new_price,
                        "new_qty": new_qty,
                    }
                    logger.log_command(cmd)
                    evs = engine1.amend_order(oid, new_price, new_qty)
                    events1.extend(evs)

        hash1 = hash_events(events1)

        # Replay the log from disk into a clean engine2
        replayer = EventReplayer(log_path)
        engine2, events2, hash2 = replayer.replay()

        # Assert exact hash match
        assert hash1 == hash2
        assert len(events1) == len(events2)

        # Assert book state match
        assert engine1.best_bid == engine2.best_bid
        assert engine1.best_ask == engine2.best_ask
        assert engine1._seq == engine2._seq
        assert engine1._trade_id == engine2._trade_id
        assert len(engine1.orders) == len(engine2.orders)
