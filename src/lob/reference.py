"""Reference Limit Order Book Matching Engine.

Deliberately naive, obviously correct implementation:
- Uses standard Python lists for bids and asks.
- Re-sorts lists on every state-changing operation.
- No heap, no deque, no caching.
- Used as the ground-truth oracle for differential fuzzing and property tests.
"""

from __future__ import annotations
from typing import Dict, List, Optional
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


class ReferenceEngine:
    """Naive reference matching engine adhering strictly to the specification."""

    def __init__(self) -> None:
        self.bids: List[Order] = []  # Sorted descending by price, ascending by seq_num
        self.asks: List[Order] = []  # Sorted ascending by price, ascending by seq_num
        self.orders: Dict[str, Order] = {}  # All orders by ID (active or historical)
        self._seq: int = 0
        self._trade_id: int = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _next_trade_id(self) -> int:
        self._trade_id += 1
        return self._trade_id

    @property
    def best_bid(self) -> Optional[int]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[int]:
        return self.asks[0].price if self.asks else None

    @property
    def is_crossed(self) -> bool:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_bid >= self.best_ask
        return False

    def get_order(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)

    def _sort_books(self) -> None:
        # Bids: highest price first, earlier seq_num first
        self.bids.sort(key=lambda o: (-o.price, o.seq_num))
        # Asks: lowest price first, earlier seq_num first
        self.asks.sort(key=lambda o: (o.price, o.seq_num))

    def process_order(
        self,
        order_id: str,
        side: Side,
        order_type: OrderType,
        quantity: int,
        price: Optional[int] = None,
    ) -> List[Event]:
        """Validate and process an incoming limit or market order."""
        events: List[Event] = []

        # 1. Validation checks
        if order_id in self.orders:
            events.append(
                RejectEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    reason=RejectReason.DUPLICATE_ORDER_ID,
                )
            )
            return events

        if not isinstance(quantity, int) or quantity <= 0:
            events.append(
                RejectEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    reason=RejectReason.INVALID_QUANTITY,
                )
            )
            return events

        if not isinstance(side, Side):
            events.append(
                RejectEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    reason=RejectReason.INVALID_SIDE,
                )
            )
            return events

        if not isinstance(order_type, OrderType):
            events.append(
                RejectEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    reason=RejectReason.INVALID_ORDER_TYPE,
                )
            )
            return events

        if order_type == OrderType.LIMIT:
            if price is None or not isinstance(price, int) or price <= 0:
                events.append(
                    RejectEvent(
                        seq_num=self._next_seq(),
                        order_id=order_id,
                        reason=RejectReason.INVALID_PRICE,
                    )
                )
                return events

        # 2. Accept order
        seq = self._next_seq()
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

        # 3. Matching
        self._match(order, events)

        # 4. Invariant check: book must never be crossed
        assert not self.is_crossed, f"Crossed book detected: bid={self.best_bid}, ask={self.best_ask}"

        return events

    def _match(self, order: Order, events: List[Event]) -> None:
        """Execute order against resting book."""
        if order.side == Side.BUY:
            opposite_book = self.asks
        else:
            opposite_book = self.bids

        while order.remaining_qty > 0 and opposite_book:
            maker = opposite_book[0]

            # Price check for limit orders
            if order.order_type == OrderType.LIMIT:
                if order.side == Side.BUY and maker.price > order.price:
                    break
                if order.side == Side.SELL and maker.price < order.price:
                    break

            # Execute trade at resting (maker) order price
            fill_qty = min(order.remaining_qty, maker.remaining_qty)
            trade_price = maker.price

            maker.remaining_qty -= fill_qty
            maker.filled_qty += fill_qty
            order.remaining_qty -= fill_qty
            order.filled_qty += fill_qty

            events.append(
                TradeEvent(
                    seq_num=self._next_seq(),
                    trade_id=self._next_trade_id(),
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
                opposite_book.pop(0)

        # Handle unfilled remainder
        if order.remaining_qty > 0:
            if order.order_type == OrderType.LIMIT:
                if order.side == Side.BUY:
                    self.bids.append(order)
                else:
                    self.asks.append(order)
                self._sort_books()
            else:
                # Market orders never rest
                unfilled = order.remaining_qty
                order.cancelled_qty += unfilled
                order.remaining_qty = 0
                events.append(
                    CancelledEvent(
                        seq_num=self._next_seq(),
                        order_id=order.order_id,
                        cancelled_qty=unfilled,
                        reason="UNFILLED_MARKET_ORDER",
                    )
                )

    def cancel_order(self, order_id: str) -> List[Event]:
        """Cancel an active order."""
        events: List[Event] = []
        order = self.orders.get(order_id)

        if order is None or order.remaining_qty == 0:
            events.append(
                RejectEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    reason=RejectReason.ORDER_NOT_FOUND,
                )
            )
            return events

        # Remove from book
        if order.side == Side.BUY:
            self.bids = [o for o in self.bids if o.order_id != order_id]
        else:
            self.asks = [o for o in self.asks if o.order_id != order_id]

        cancelled_qty = order.remaining_qty
        order.cancelled_qty += cancelled_qty
        order.remaining_qty = 0

        events.append(
            CancelledEvent(
                seq_num=self._next_seq(),
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
            events.append(
                RejectEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    reason=RejectReason.INVALID_AMEND_PARAMS,
                )
            )
            return events

        order = self.orders.get(order_id)
        if order is None or order.remaining_qty == 0:
            events.append(
                RejectEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    reason=RejectReason.ORDER_NOT_FOUND,
                )
            )
            return events

        old_price = order.price
        old_qty = order.remaining_qty

        # Case 1: No change
        if new_price == old_price and new_qty == old_qty:
            events.append(
                AmendedEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    old_price=old_price,
                    new_price=new_price,
                    old_qty=old_qty,
                    new_qty=new_qty,
                    priority_lost=False,
                )
            )
            return events

        # Case 2: Quantity reduction at identical price -> retains priority
        if new_price == old_price and new_qty < old_qty:
            diff = old_qty - new_qty
            order.cancelled_qty += diff
            order.remaining_qty = new_qty
            events.append(
                AmendedEvent(
                    seq_num=self._next_seq(),
                    order_id=order_id,
                    old_price=old_price,
                    new_price=new_price,
                    old_qty=old_qty,
                    new_qty=new_qty,
                    priority_lost=False,
                )
            )
            return events

        # Case 3: Price changed OR quantity increased -> loses priority
        # Remove from current book position
        if order.side == Side.BUY:
            self.bids = [o for o in self.bids if o.order_id != order_id]
        else:
            self.asks = [o for o in self.asks if o.order_id != order_id]

        if new_qty > old_qty:
            order.original_qty += (new_qty - old_qty)
        else:
            order.cancelled_qty += (old_qty - new_qty)

        new_seq = self._next_seq()
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

        # Match aggressively if crosses
        self._match(order, events)

        assert not self.is_crossed, f"Crossed book detected after amend: bid={self.best_bid}, ask={self.best_ask}"
        return events
