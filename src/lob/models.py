"""Core domain models, enums, and event definitions for the Limit Order Book."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class Side(str, Enum):
    """Order side: BUY (Bid) or SELL (Ask)."""
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self == Side.BUY else Side.BUY


class OrderType(str, Enum):
    """Supported order execution instructions."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    """Lifecycle status of an order."""
    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class RejectReason(str, Enum):
    """Reason codes for rejected operations."""
    DUPLICATE_ORDER_ID = "DUPLICATE_ORDER_ID"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_SIDE = "INVALID_SIDE"
    INVALID_ORDER_TYPE = "INVALID_ORDER_TYPE"
    ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
    INVALID_AMEND_PARAMS = "INVALID_AMEND_PARAMS"


@dataclass(slots=True)
class Order:
    """Internal order representation for resting and tracking orders.

    All numerical fields are strictly integers.
    Uses __slots__ for minimal memory footprint and zero per-instance __dict__.
    """
    order_id: str
    side: Side
    order_type: OrderType
    price: int | None
    original_qty: int
    remaining_qty: int
    filled_qty: int = 0
    cancelled_qty: int = 0
    seq_num: int = 0

    @property
    def is_filled(self) -> bool:
        return self.remaining_qty == 0

    @property
    def is_active(self) -> bool:
        return self.remaining_qty > 0


# ============================================================================
# Event Model Definitions (Emitted by Engine operations)
# ============================================================================

@dataclass(slots=True, frozen=True)
class Event:
    """Base class for all matching engine emitted events."""
    seq_num: int

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError


@dataclass(slots=True, frozen=True)
class AckEvent(Event):
    """Emitted when an order passes validation and enters the engine."""
    order_id: str
    side: Side
    order_type: OrderType
    price: int | None
    quantity: int
    remaining_qty: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "ACK",
            "seq_num": self.seq_num,
            "order_id": self.order_id,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "price": self.price,
            "quantity": self.quantity,
            "remaining_qty": self.remaining_qty,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AckEvent:
        return cls(
            seq_num=data["seq_num"],
            order_id=data["order_id"],
            side=Side(data["side"]),
            order_type=OrderType(data["order_type"]),
            price=data["price"],
            quantity=data["quantity"],
            remaining_qty=data["remaining_qty"],
        )


@dataclass(slots=True, frozen=True)
class RejectEvent(Event):
    """Emitted when an operation fails validation."""
    order_id: str
    reason: RejectReason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "REJECT",
            "seq_num": self.seq_num,
            "order_id": self.order_id,
            "reason": self.reason.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RejectEvent:
        return cls(
            seq_num=data["seq_num"],
            order_id=data["order_id"],
            reason=RejectReason(data["reason"]),
        )


@dataclass(slots=True, frozen=True)
class TradeEvent(Event):
    """Emitted when two orders match. Execution price is always maker's price."""
    trade_id: int
    maker_order_id: str
    taker_order_id: str
    maker_side: Side
    price: int
    quantity: int
    maker_remaining_qty: int
    taker_remaining_qty: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "TRADE",
            "seq_num": self.seq_num,
            "trade_id": self.trade_id,
            "maker_order_id": self.maker_order_id,
            "taker_order_id": self.taker_order_id,
            "maker_side": self.maker_side.value,
            "price": self.price,
            "quantity": self.quantity,
            "maker_remaining_qty": self.maker_remaining_qty,
            "taker_remaining_qty": self.taker_remaining_qty,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TradeEvent:
        return cls(
            seq_num=data["seq_num"],
            trade_id=data["trade_id"],
            maker_order_id=data["maker_order_id"],
            taker_order_id=data["taker_order_id"],
            maker_side=Side(data["maker_side"]),
            price=data["price"],
            quantity=data["quantity"],
            maker_remaining_qty=data["maker_remaining_qty"],
            taker_remaining_qty=data["taker_remaining_qty"],
        )


@dataclass(slots=True, frozen=True)
class CancelledEvent(Event):
    """Emitted when a resting order or unfilled market order is cancelled."""
    order_id: str
    cancelled_qty: int
    reason: str = "CLIENT_REQUEST"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "CANCELLED",
            "seq_num": self.seq_num,
            "order_id": self.order_id,
            "cancelled_qty": self.cancelled_qty,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CancelledEvent:
        return cls(
            seq_num=data["seq_num"],
            order_id=data["order_id"],
            cancelled_qty=data["cancelled_qty"],
            reason=data.get("reason", "CLIENT_REQUEST"),
        )


@dataclass(slots=True, frozen=True)
class AmendedEvent(Event):
    """Emitted when an active order's price or quantity is amended."""
    order_id: str
    old_price: int
    new_price: int
    old_qty: int
    new_qty: int
    priority_lost: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "AMENDED",
            "seq_num": self.seq_num,
            "order_id": self.order_id,
            "old_price": self.old_price,
            "new_price": self.new_price,
            "old_qty": self.old_qty,
            "new_qty": self.new_qty,
            "priority_lost": self.priority_lost,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AmendedEvent:
        return cls(
            seq_num=data["seq_num"],
            order_id=data["order_id"],
            old_price=data["old_price"],
            new_price=data["new_price"],
            old_qty=data["old_qty"],
            new_qty=data["new_qty"],
            priority_lost=data["priority_lost"],
        )


def event_from_dict(data: Dict[str, Any]) -> Event:
    """Deserialize a dictionary back into the appropriate Event variant."""
    event_type = data.get("type")
    if event_type == "ACK":
        return AckEvent.from_dict(data)
    elif event_type == "REJECT":
        return RejectEvent.from_dict(data)
    elif event_type == "TRADE":
        return TradeEvent.from_dict(data)
    elif event_type == "CANCELLED":
        return CancelledEvent.from_dict(data)
    elif event_type == "AMENDED":
        return AmendedEvent.from_dict(data)
    else:
        raise ValueError(f"Unknown event type: {event_type}")
