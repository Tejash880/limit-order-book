"""Basic unit tests covering all 12 core rules and edge cases."""

import pytest
from lob.models import (
    Side,
    OrderType,
    RejectReason,
    AckEvent,
    RejectEvent,
    TradeEvent,
    CancelledEvent,
    AmendedEvent,
)
from lob.reference import ReferenceEngine
from lob.engine import MatchingEngine

# Test both engines through the exact same comprehensive test suite
ENGINE_CLASSES = [ReferenceEngine, MatchingEngine]


@pytest.fixture(params=ENGINE_CLASSES)
def engine(request):
    return request.param()


def test_rule_1_and_4_limit_order_placement_and_resting(engine):
    """Rule 1 & 4: Limit orders rest on the book when not crossed."""
    events = engine.process_order("o1", Side.BUY, OrderType.LIMIT, 10, 100)
    assert len(events) == 1
    assert isinstance(events[0], AckEvent)
    assert events[0].order_id == "o1"
    assert engine.best_bid == 100
    assert engine.best_ask is None

    events2 = engine.process_order("o2", Side.SELL, OrderType.LIMIT, 5, 105)
    assert len(events2) == 1
    assert isinstance(events2[0], AckEvent)
    assert engine.best_bid == 100
    assert engine.best_ask == 105
    assert not engine.is_crossed


def test_rule_2_price_time_priority_fifo(engine):
    """Rule 2: Higher bids and lower asks match first. Within same price, FIFO."""
    # Place two asks at 100: o1 first, o2 second
    engine.process_order("o1", Side.SELL, OrderType.LIMIT, 10, 100)
    engine.process_order("o2", Side.SELL, OrderType.LIMIT, 10, 100)
    # Place an ask at 101: o3
    engine.process_order("o3", Side.SELL, OrderType.LIMIT, 10, 101)

    # Buy order arrives with quantity 15 at price 100
    # Should match ALL 10 of o1, and 5 of o2 (FIFO at 100)
    events = engine.process_order("taker1", Side.BUY, OrderType.LIMIT, 15, 100)
    trades = [e for e in events if isinstance(e, TradeEvent)]
    assert len(trades) == 2

    # First trade against o1
    assert trades[0].maker_order_id == "o1"
    assert trades[0].quantity == 10
    assert trades[0].price == 100
    assert trades[0].maker_remaining_qty == 0

    # Second trade against o2
    assert trades[1].maker_order_id == "o2"
    assert trades[1].quantity == 5
    assert trades[1].price == 100
    assert trades[1].maker_remaining_qty == 5

    # o2 still has 5 left at 100
    assert engine.best_ask == 100


def test_rule_3_maker_price_rule(engine):
    """Rule 3: Trade price is always the resting (maker) order's price."""
    # Resting sell at 100
    engine.process_order("maker_sell", Side.SELL, OrderType.LIMIT, 10, 100)
    # Incoming buy at 105 (willing to pay up to 105)
    events = engine.process_order("taker_buy", Side.BUY, OrderType.LIMIT, 10, 105)

    trades = [e for e in events if isinstance(e, TradeEvent)]
    assert len(trades) == 1
    # Trade executed at 100 (maker's price), not 105
    assert trades[0].price == 100

    # Opposite test: Resting buy at 105, incoming sell at 100
    engine.process_order("maker_buy", Side.BUY, OrderType.LIMIT, 10, 105)
    events2 = engine.process_order("taker_sell", Side.SELL, OrderType.LIMIT, 10, 100)
    trades2 = [e for e in events2 if isinstance(e, TradeEvent)]
    assert len(trades2) == 1
    # Trade executed at 105 (maker's price), not 100
    assert trades2[0].price == 105


def test_rule_5_market_order_and_cancellation_remainder(engine):
    """Rule 5: Market order executes against available liquidity; remainder cancels immediately."""
    engine.process_order("ask1", Side.SELL, OrderType.LIMIT, 10, 100)
    engine.process_order("ask2", Side.SELL, OrderType.LIMIT, 10, 102)

    # Market buy for 25: fills 10 at 100, 10 at 102, 5 cancelled
    events = engine.process_order("mkt_buy", Side.BUY, OrderType.MARKET, 25)
    acks = [e for e in events if isinstance(e, AckEvent)]
    trades = [e for e in events if isinstance(e, TradeEvent)]
    cancels = [e for e in events if isinstance(e, CancelledEvent)]

    assert len(acks) == 1
    assert len(trades) == 2
    assert trades[0].price == 100 and trades[0].quantity == 10
    assert trades[1].price == 102 and trades[1].quantity == 10

    assert len(cancels) == 1
    assert cancels[0].cancelled_qty == 5
    assert cancels[0].reason == "UNFILLED_MARKET_ORDER"

    # Book is now empty on the ask side
    assert engine.best_ask is None


def test_rule_5_market_order_on_empty_book(engine):
    """Rule 5: Market order on empty book receives ACK then immediate CANCELLED."""
    events = engine.process_order("mkt1", Side.BUY, OrderType.MARKET, 50)
    assert len(events) == 2
    assert isinstance(events[0], AckEvent)
    assert isinstance(events[1], CancelledEvent)
    assert events[1].cancelled_qty == 50


def test_rule_6_partial_fills_across_three_levels(engine):
    """Rule 6: Sweeping across 3 price levels."""
    engine.process_order("a1", Side.SELL, OrderType.LIMIT, 5, 100)
    engine.process_order("a2", Side.SELL, OrderType.LIMIT, 10, 101)
    engine.process_order("a3", Side.SELL, OrderType.LIMIT, 15, 102)

    # Aggressive buy for 20 at 102 sweeps all of 100 (5), all of 101 (10), and 5 of 102
    events = engine.process_order("buyer", Side.BUY, OrderType.LIMIT, 20, 102)
    trades = [e for e in events if isinstance(e, TradeEvent)]
    assert len(trades) == 3
    assert trades[0].quantity == 5 and trades[0].price == 100
    assert trades[1].quantity == 10 and trades[1].price == 101
    assert trades[2].quantity == 5 and trades[2].price == 102

    # Level 102 still has 10 remaining
    assert engine.best_ask == 102


def test_rule_7_cancellation(engine):
    """Rule 7: Cancel resting order; unknown/finished order rejects cleanly."""
    engine.process_order("o1", Side.BUY, OrderType.LIMIT, 10, 100)
    events = engine.cancel_order("o1")
    assert len(events) == 1
    assert isinstance(events[0], CancelledEvent)
    assert events[0].cancelled_qty == 10
    assert engine.best_bid is None

    # Cancelling again must reject with ORDER_NOT_FOUND, not crash
    events_again = engine.cancel_order("o1")
    assert len(events_again) == 1
    assert isinstance(events_again[0], RejectEvent)
    assert events_again[0].reason == RejectReason.ORDER_NOT_FOUND

    # Cancelling non-existent order
    events_unknown = engine.cancel_order("unknown")
    assert len(events_unknown) == 1
    assert isinstance(events_unknown[0], RejectEvent)
    assert events_unknown[0].reason == RejectReason.ORDER_NOT_FOUND


def test_rule_7_cancel_after_partial_fill(engine):
    """Rule 7: Cancelling an order that was partially filled cancels only remainder."""
    engine.process_order("sell1", Side.SELL, OrderType.LIMIT, 20, 100)
    engine.process_order("buy1", Side.BUY, OrderType.LIMIT, 12, 100)

    # sell1 has 8 remaining
    events = engine.cancel_order("sell1")
    assert len(events) == 1
    assert isinstance(events[0], CancelledEvent)
    assert events[0].cancelled_qty == 8
    assert engine.best_ask is None


def test_rule_8_amend_quantity_decrease_retains_priority(engine):
    """Rule 8: Decreasing quantity at same price retains FIFO priority."""
    engine.process_order("s1", Side.SELL, OrderType.LIMIT, 10, 100)
    engine.process_order("s2", Side.SELL, OrderType.LIMIT, 10, 100)

    # s1 decreases from 10 to 6
    events = engine.amend_order("s1", 100, 6)
    assert len(events) == 1
    assert isinstance(events[0], AmendedEvent)
    assert events[0].priority_lost is False
    assert events[0].new_qty == 6

    # Incoming buy for 8 should fill all 6 of s1, and 2 of s2
    events2 = engine.process_order("b1", Side.BUY, OrderType.LIMIT, 8, 100)
    trades = [e for e in events2 if isinstance(e, TradeEvent)]
    assert trades[0].maker_order_id == "s1"
    assert trades[0].quantity == 6
    assert trades[1].maker_order_id == "s2"
    assert trades[1].quantity == 2


def test_rule_8_amend_quantity_increase_loses_priority(engine):
    """Rule 8: Increasing quantity loses FIFO priority."""
    engine.process_order("s1", Side.SELL, OrderType.LIMIT, 10, 100)
    engine.process_order("s2", Side.SELL, OrderType.LIMIT, 10, 100)

    # s1 increases to 15 -> loses priority, moves behind s2
    events = engine.amend_order("s1", 100, 15)
    assert len(events) == 1
    assert isinstance(events[0], AmendedEvent)
    assert events[0].priority_lost is True

    # Incoming buy for 12 should fill s2 FIRST (10), then s1 (2)
    events2 = engine.process_order("b1", Side.BUY, OrderType.LIMIT, 12, 100)
    trades = [e for e in events2 if isinstance(e, TradeEvent)]
    assert trades[0].maker_order_id == "s2"
    assert trades[0].quantity == 10
    assert trades[1].maker_order_id == "s1"
    assert trades[1].quantity == 2


def test_rule_8_amend_price_change_can_match_immediately(engine):
    """Rule 8: Changing price to cross the book executes immediately."""
    engine.process_order("b1", Side.BUY, OrderType.LIMIT, 10, 100)
    engine.process_order("s1", Side.SELL, OrderType.LIMIT, 10, 105)

    # Amend s1 price to 100 (crosses b1)
    events = engine.amend_order("s1", 100, 10)
    assert any(isinstance(e, AmendedEvent) for e in events)
    trades = [e for e in events if isinstance(e, TradeEvent)]
    assert len(trades) == 1
    # Trade executes at resting order b1's price (100)
    assert trades[0].price == 100
    assert trades[0].quantity == 10
    assert not engine.is_crossed


def test_rule_9_validation_rejections(engine):
    """Rule 9: Duplicate ID, non-positive quantity, non-positive price reject cleanly."""
    # 1. Non-positive quantity
    e1 = engine.process_order("o1", Side.BUY, OrderType.LIMIT, 0, 100)
    assert isinstance(e1[0], RejectEvent)
    assert e1[0].reason == RejectReason.INVALID_QUANTITY

    e2 = engine.process_order("o2", Side.BUY, OrderType.LIMIT, -5, 100)
    assert isinstance(e2[0], RejectEvent)
    assert e2[0].reason == RejectReason.INVALID_QUANTITY

    # 2. Non-positive price
    e3 = engine.process_order("o3", Side.BUY, OrderType.LIMIT, 10, 0)
    assert isinstance(e3[0], RejectEvent)
    assert e3[0].reason == RejectReason.INVALID_PRICE

    e4 = engine.process_order("o4", Side.BUY, OrderType.LIMIT, 10, -50)
    assert isinstance(e4[0], RejectEvent)
    assert e4[0].reason == RejectReason.INVALID_PRICE

    # 3. Duplicate order_id
    engine.process_order("unique_id", Side.BUY, OrderType.LIMIT, 10, 100)
    e5 = engine.process_order("unique_id", Side.SELL, OrderType.LIMIT, 10, 105)
    assert isinstance(e5[0], RejectEvent)
    assert e5[0].reason == RejectReason.DUPLICATE_ORDER_ID


def test_rule_10_self_trade_allowed(engine):
    """Rule 10: Self-trades are not prevented."""
    # Same client identifier or same side matching
    engine.process_order("trader1_bid", Side.BUY, OrderType.LIMIT, 10, 100)
    events = engine.process_order("trader1_ask", Side.SELL, OrderType.LIMIT, 10, 100)
    trades = [e for e in events if isinstance(e, TradeEvent)]
    assert len(trades) == 1
    assert trades[0].maker_order_id == "trader1_bid"
    assert trades[0].taker_order_id == "trader1_ask"


def test_rule_11_book_never_crossed(engine):
    """Rule 11: After any operation, best_bid < best_ask."""
    engine.process_order("b1", Side.BUY, OrderType.LIMIT, 10, 100)
    engine.process_order("s1", Side.SELL, OrderType.LIMIT, 10, 102)
    assert engine.best_bid < engine.best_ask

    # Cross with an aggressive buy at 105
    engine.process_order("b2", Side.BUY, OrderType.LIMIT, 5, 105)
    # Remaining ask at 102 is now 5
    assert engine.best_bid == 100
    assert engine.best_ask == 102
    assert engine.best_bid < engine.best_ask
