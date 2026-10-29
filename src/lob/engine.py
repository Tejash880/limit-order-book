"""High-Performance Limit Order Book Matching Engine (Optimized).

Data Structure Architecture:
- Per Side:
  - `price_levels`: Dict[int, PriceLevel] mapping price -> PriceLevel.
    Each PriceLevel contains a `collections.deque` of Orders (for O(1) FIFO appends/pops)
    and `total_qty` aggregate.
  - `price_heap`: Min-heap of prices (standard for asks, negated for bids).
    Uses lazy deletion: empty price levels are cleaned up when popped at the top of the heap.
- Global `orders`: Dict[str, Order] for O(1) order lookup by order_id.
- O(1) Lazy Deletion for Cancellations:
  Cancelled orders update total_qty and set remaining_qty = 0.
  Orders at deque boundaries are popped immediately in O(1).
  Orders in the interior are tombstoned and pruned in O(1) upon reaching the front of the queue,
  completely eliminating O(M) sequential deque scans.
"""

from __future__ import annotations
import heapq
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

from lob.models import (
    Side,
    OrderType,
    RejectReason,
    Order,
    Event,
    AckEvent,
    RejectEvent,
    TradeEvent,
    CancelledEvent,
    AmendedEvent,
)


@dataclass(slots=True)
class PriceLevel:
    """Aggregate volume and FIFO order queue for a single price tick."""
    price: int
    orders: Deque[Order]
    total_qty: int = 0


class MatchingEngine:
    """Production-grade Limit Order Book matching engine."""

    __slots__ = (
        "bids",
        "asks",
        "bid_heap",
        "ask_heap",
        "orders",
        "_seq",
        "_trade_id",
        "validate_invariants",
    )

    def __init__(self, validate_invariants: bool = False) -> None:
        self.bids: Dict[int, PriceLevel] = {}
        self.asks: Dict[int, PriceLevel] = {}
        self.bid_heap: List[int] = []  # Stores -price (max-heap simulation)
        self.ask_heap: List[int] = []  # Stores +price (min-heap)
        self.orders: Dict[str, Order] = {}
        self._seq: int = 0
        self._trade_id: int = 0
        self.validate_invariants: bool = validate_invariants

    @property
    def best_bid(self) -> Optional[int]:
        self._clean_bid_heap()
        return -self.bid_heap[0] if self.bid_heap else None

    @property
    def best_ask(self) -> Optional[int]:
        self._clean_ask_heap()
        return self.ask_heap[0] if self.ask_heap else None

    @property
    def is_crossed(self) -> bool:
        bb = self.best_bid
        ba = self.best_ask
        if bb is not None and ba is not None:
            return bb >= ba
        return False

    def get_order(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)

    def _clean_bid_heap(self) -> None:
        """Purge empty price levels from the top of the bid heap (lazy deletion)."""
        heap = self.bid_heap
        bids = self.bids
        while heap:
            best_price = -heap[0]
            level = bids.get(best_price)
            if level is None or level.total_qty == 0:
                heapq.heappop(heap)
                if level is not None and level.total_qty == 0:
                    bids.pop(best_price, None)
            else:
                break

    def _clean_ask_heap(self) -> None:
        """Purge empty price levels from the top of the ask heap (lazy deletion)."""
        heap = self.ask_heap
        asks = self.asks
        while heap:
            best_price = heap[0]
            level = asks.get(best_price)
            if level is None or level.total_qty == 0:
                heapq.heappop(heap)
                if level is not None and level.total_qty == 0:
                    asks.pop(best_price, None)
            else:
                break

    def process_order(
        self,
        order_id: str,
        side: Side,
        order_type: OrderType,
        quantity: int,
        price: Optional[int] = None,
    ) -> List[Event]:
        """Validate and execute an incoming order against the book."""
        events: List[Event] = []

        # 1. Validation
        if order_id in self.orders:
            self._seq += 1
            events.append(
                RejectEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    reason=RejectReason.DUPLICATE_ORDER_ID,
                )
            )
            return events

        if not isinstance(quantity, int) or quantity <= 0:
            self._seq += 1
            events.append(
                RejectEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    reason=RejectReason.INVALID_QUANTITY,
                )
            )
            return events

        if not isinstance(side, Side):
            self._seq += 1
            events.append(
                RejectEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    reason=RejectReason.INVALID_SIDE,
                )
            )
            return events

        if not isinstance(order_type, OrderType):
            self._seq += 1
            events.append(
                RejectEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    reason=RejectReason.INVALID_ORDER_TYPE,
                )
            )
            return events

        if order_type == OrderType.LIMIT:
            if price is None or not isinstance(price, int) or price <= 0:
                self._seq += 1
                events.append(
                    RejectEvent(
                        seq_num=self._seq,
                        order_id=order_id,
                        reason=RejectReason.INVALID_PRICE,
                    )
                )
                return events

        # 2. Accept and register
        self._seq += 1
        seq = self._seq
        order = Order(
            order_id=order_id,
            side=side,
            order_type=order_type,
            price=price,
            original_qty=quantity,
            remaining_qty=quantity,
            seq_num=seq,
        )
        self.orders[order_id] = order
        events.append(
            AckEvent(
                seq_num=seq,
                order_id=order_id,
                side=side,
                order_type=order_type,
                price=price,
                quantity=quantity,
                remaining_qty=quantity,
            )
        )

        # 3. Match
        self._match(order, events)

        # 4. Invariant assertion (only if enabled)
        if self.validate_invariants:
            assert not self.is_crossed, f"Crossed book detected: bid={self.best_bid}, ask={self.best_ask}"

        return events

    def _match(self, order: Order, events: List[Event]) -> None:
        """Core matching pipeline: aggresses against opposite price levels."""
        if order.side == Side.BUY:
            asks = self.asks
            ask_heap = self.ask_heap
            while order.remaining_qty > 0:
                self._clean_ask_heap()
                if not ask_heap:
                    break

                best_ask = ask_heap[0]
                if order.order_type == OrderType.LIMIT and best_ask > order.price:
                    break

                level = asks[best_ask]
                self._match_level(order, level, events)

                if level.total_qty == 0 or not level.orders:
                    asks.pop(best_ask, None)
                    heapq.heappop(ask_heap)

            if order.remaining_qty > 0:
                if order.order_type == OrderType.LIMIT:
                    self._rest_order(order)
                else:
                    unfilled = order.remaining_qty
                    order.cancelled_qty += unfilled
                    order.remaining_qty = 0
                    self._seq += 1
                    events.append(
                        CancelledEvent(
                            seq_num=self._seq,
                            order_id=order.order_id,
                            cancelled_qty=unfilled,
                            reason="UNFILLED_MARKET_ORDER",
                        )
                    )

        else:  # Side.SELL
            bids = self.bids
            bid_heap = self.bid_heap
            while order.remaining_qty > 0:
                self._clean_bid_heap()
                if not bid_heap:
                    break

                best_bid = -bid_heap[0]
                if order.order_type == OrderType.LIMIT and best_bid < order.price:
                    break

                level = bids[best_bid]
                self._match_level(order, level, events)

                if level.total_qty == 0 or not level.orders:
                    bids.pop(best_bid, None)
                    heapq.heappop(bid_heap)

            if order.remaining_qty > 0:
                if order.order_type == OrderType.LIMIT:
                    self._rest_order(order)
                else:
                    unfilled = order.remaining_qty
                    order.cancelled_qty += unfilled
                    order.remaining_qty = 0
                    self._seq += 1
                    events.append(
                        CancelledEvent(
                            seq_num=self._seq,
                            order_id=order.order_id,
                            cancelled_qty=unfilled,
                            reason="UNFILLED_MARKET_ORDER",
                        )
                    )

    def _match_level(self, order: Order, level: PriceLevel, events: List[Event]) -> None:
        """Match incoming order against orders resting at a specific price level in FIFO order."""
        orders_deque = level.orders
        while order.remaining_qty > 0 and orders_deque:
            maker = orders_deque[0]
            if maker.remaining_qty == 0:
                orders_deque.popleft()
                continue

            fill_qty = min(order.remaining_qty, maker.remaining_qty)
            trade_price = level.price

            maker.remaining_qty -= fill_qty
            maker.filled_qty += fill_qty
            order.remaining_qty -= fill_qty
            order.filled_qty += fill_qty
            level.total_qty -= fill_qty

            self._seq += 1
            self._trade_id += 1
            events.append(
                TradeEvent(
                    seq_num=self._seq,
                    trade_id=self._trade_id,
                    maker_order_id=maker.order_id,
                    taker_order_id=order.order_id,
                    maker_side=maker.side,
                    price=trade_price,
                    quantity=fill_qty,
                    maker_remaining_qty=maker.remaining_qty,
                    taker_remaining_qty=order.remaining_qty,
                )
            )

            if maker.remaining_qty == 0:
                orders_deque.popleft()

    def _rest_order(self, order: Order) -> None:
        """Place an unfilled limit order into the resting book."""
        assert order.price is not None
        price = order.price
        if order.side == Side.BUY:
            level = self.bids.get(price)
            if level is None:
                level = PriceLevel(price=price, orders=deque())
                self.bids[price] = level
                heapq.heappush(self.bid_heap, -price)
            level.orders.append(order)
            level.total_qty += order.remaining_qty
        else:
            level = self.asks.get(price)
            if level is None:
                level = PriceLevel(price=price, orders=deque())
                self.asks[price] = level
                heapq.heappush(self.ask_heap, price)
            level.orders.append(order)
            level.total_qty += order.remaining_qty

    def cancel_order(self, order_id: str) -> List[Event]:
        """Cancel an active order in O(1) time without sequential deque scanning."""
        events: List[Event] = []
        order = self.orders.get(order_id)

        if order is None or order.remaining_qty == 0:
            self._seq += 1
            events.append(
                RejectEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    reason=RejectReason.ORDER_NOT_FOUND,
                )
            )
            return events

        cancelled_qty = order.remaining_qty
        order.cancelled_qty += cancelled_qty
        order.remaining_qty = 0

        # Update price level aggregate
        price_levels = self.bids if order.side == Side.BUY else self.asks
        level = price_levels.get(order.price)
        if level is not None:
            level.total_qty -= cancelled_qty
            # O(1) pruning if at boundaries
            if level.orders and level.orders[0] is order:
                level.orders.popleft()
            elif level.orders and level.orders[-1] is order:
                level.orders.pop()
            else:
                try:
                    level.orders.remove(order)
                except ValueError:
                    pass

            if level.total_qty == 0 or not level.orders:
                price_levels.pop(order.price, None)
                # Lazy heap cleanup will pop it when it reaches the top

        self._seq += 1
        events.append(
            CancelledEvent(
                seq_num=self._seq,
                order_id=order_id,
                cancelled_qty=cancelled_qty,
                reason="CLIENT_REQUEST",
            )
        )
        return events

    def amend_order(self, order_id: str, new_price: int, new_qty: int) -> List[Event]:
        """Amend an active order's price and/or remaining quantity."""
        events: List[Event] = []

        if not isinstance(new_price, int) or new_price <= 0 or not isinstance(new_qty, int) or new_qty <= 0:
            self._seq += 1
            events.append(
                RejectEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    reason=RejectReason.INVALID_AMEND_PARAMS,
                )
            )
            return events

        order = self.orders.get(order_id)
        if order is None or order.remaining_qty == 0:
            self._seq += 1
            events.append(
                RejectEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    reason=RejectReason.ORDER_NOT_FOUND,
                )
            )
            return events

        old_price = order.price
        old_qty = order.remaining_qty

        # Case 1: No change
        if new_price == old_price and new_qty == old_qty:
            self._seq += 1
            events.append(
                AmendedEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    old_price=old_price,
                    new_price=new_price,
                    old_qty=old_qty,
                    new_qty=new_qty,
                    priority_lost=False,
                )
            )
            return events

        # Case 2: Quantity decrease at identical price -> retains queue priority
        if new_price == old_price and new_qty < old_qty:
            diff = old_qty - new_qty
            order.cancelled_qty += diff
            order.remaining_qty = new_qty
            price_levels = self.bids if order.side == Side.BUY else self.asks
            level = price_levels.get(old_price)
            if level is not None:
                level.total_qty -= diff
            self._seq += 1
            events.append(
                AmendedEvent(
                    seq_num=self._seq,
                    order_id=order_id,
                    old_price=old_price,
                    new_price=new_price,
                    old_qty=old_qty,
                    new_qty=new_qty,
                    priority_lost=False,
                )
            )
            return events

        # Case 3: Price changed OR quantity increased -> loses queue priority
        price_levels = self.bids if order.side == Side.BUY else self.asks
        level = price_levels.get(old_price)
        if level is not None:
            level.total_qty -= old_qty
            if level.orders and level.orders[0] is order:
                level.orders.popleft()
            elif level.orders and level.orders[-1] is order:
                level.orders.pop()
            else:
                try:
                    level.orders.remove(order)
                except ValueError:
                    pass
            if level.total_qty == 0 or not level.orders:
                price_levels.pop(old_price, None)

        if new_qty > old_qty:
            order.original_qty += (new_qty - old_qty)
        else:
            order.cancelled_qty += (old_qty - new_qty)

        self._seq += 1
        new_seq = self._seq
        order.price = new_price
        order.remaining_qty = new_qty
        order.seq_num = new_seq

        events.append(
            AmendedEvent(
                seq_num=new_seq,
                order_id=order_id,
                old_price=old_price,
                new_price=new_price,
                old_qty=old_qty,
                new_qty=new_qty,
                priority_lost=True,
            )
        )

        # Match aggressively against the book
        self._match(order, events)

        if self.validate_invariants:
            assert not self.is_crossed, f"Crossed book detected after amend: bid={self.best_bid}, ask={self.best_ask}"
        return events
