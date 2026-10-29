"""Property-based invariant tests using Hypothesis."""

from hypothesis import given, settings, strategies as st
from lob.models import (
    Side,
    OrderType,
    TradeEvent,
)
from lob.engine import MatchingEngine


# Strategy for generating valid order actions
action_strategy = st.one_of(
    st.tuples(
        st.just("LIMIT"),
        st.sampled_from([Side.BUY, Side.SELL]),
        st.integers(min_value=1, max_value=100),
        st.integers(min_value=1, max_value=50),
    ),
    st.tuples(
        st.just("MARKET"),
        st.sampled_from([Side.BUY, Side.SELL]),
        st.integers(min_value=1, max_value=100),
        st.none(),
    ),
    st.tuples(
        st.just("CANCEL"),
        st.integers(min_value=0, max_value=100),  # index into existing orders
        st.none(),
        st.none(),
    ),
    st.tuples(
        st.just("AMEND"),
        st.integers(min_value=0, max_value=100),  # index into existing orders
        st.integers(min_value=1, max_value=100),  # new_qty
        st.integers(min_value=1, max_value=50),   # new_price
    ),
)


@settings(max_examples=100, deadline=None)
@given(st.lists(action_strategy, min_size=5, max_size=100))
def test_matching_engine_invariants(actions):
    """Verify all formal invariant properties hold continuously under randomized operations."""
    engine = MatchingEngine()
    submitted_order_ids = []
    order_counter = 0

    for action in actions:
        action_type = action[0]

        if action_type in ("LIMIT", "MARKET"):
            order_counter += 1
            oid = f"ord_{order_counter}"
            side = action[1]
            qty = action[2]
            price = action[3]
            order_type = OrderType.LIMIT if action_type == "LIMIT" else OrderType.MARKET
            events = engine.process_order(oid, side, order_type, qty, price)
            submitted_order_ids.append(oid)

        elif action_type == "CANCEL":
            if not submitted_order_ids:
                continue
            idx = action[1] % len(submitted_order_ids)
            oid = submitted_order_ids[idx]
            events = engine.cancel_order(oid)

        elif action_type == "AMEND":
            if not submitted_order_ids:
                continue
            idx = action[1] % len(submitted_order_ids)
            oid = submitted_order_ids[idx]
            new_qty = action[2]
            new_price = action[3]
            events = engine.amend_order(oid, new_price, new_qty)

        # -------------------------------------------------------------
        # Invariant 1: Uncrossed Book
        # -------------------------------------------------------------
        assert not engine.is_crossed, (
            f"Crossed book: best_bid={engine.best_bid}, best_ask={engine.best_ask}"
        )
        if engine.best_bid is not None and engine.best_ask is not None:
            assert engine.best_bid < engine.best_ask

        # -------------------------------------------------------------
        # Invariant 2: Trade Properties
        # -------------------------------------------------------------
        for event in events:
            if isinstance(event, TradeEvent):
                assert event.quantity > 0, "Trade quantity must be strictly positive"
                assert event.price > 0, "Trade price must be strictly positive"
                maker_ord = engine.get_order(event.maker_order_id)
                assert maker_ord is not None
                # Trade price must equal resting maker order's price
                assert event.price == maker_ord.price

    # -------------------------------------------------------------
    # Invariant 3: Global Order Invariants
    # -------------------------------------------------------------
    for oid in submitted_order_ids:
        order = engine.get_order(oid)
        assert order is not None

        # 3a. Conservation of Volume: original == filled + remaining + cancelled
        assert order.original_qty == order.filled_qty + order.remaining_qty + order.cancelled_qty, (
            f"Volume conservation failed for {oid}: "
            f"orig={order.original_qty}, filled={order.filled_qty}, rem={order.remaining_qty}, can={order.cancelled_qty}"
        )

        # 3b. Non-negativity
        assert order.filled_qty >= 0
        assert order.remaining_qty >= 0
        assert order.cancelled_qty >= 0

        # 3c. Overfill protection
        assert order.filled_qty <= order.original_qty

    # -------------------------------------------------------------
    # Invariant 4: Resting Book Consistency and FIFO Ordering
    # -------------------------------------------------------------
    for price, level in engine.bids.items():
        assert level.total_qty == sum(o.remaining_qty for o in level.orders if o.remaining_qty > 0)
        # Check FIFO sequence numbers are monotonically increasing
        seqs = [o.seq_num for o in level.orders if o.remaining_qty > 0]
        assert seqs == sorted(seqs)

    for price, level in engine.asks.items():
        assert level.total_qty == sum(o.remaining_qty for o in level.orders if o.remaining_qty > 0)
        seqs = [o.seq_num for o in level.orders if o.remaining_qty > 0]
        assert seqs == sorted(seqs)
