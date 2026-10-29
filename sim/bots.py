"""Agent-based simulation bots interacting with the matching engine."""

from __future__ import annotations
import random
from typing import Dict, List, Optional, Tuple, Any
from lob.models import Side, OrderType


class BaseBot:
    """Base class for trading agents in the simulation."""

    def __init__(self, bot_id: str, initial_cash: int = 1_000_000) -> None:
        self.bot_id = bot_id
        self.cash: int = initial_cash
        self.inventory: int = 0  # Net position in lots/shares
        self.history_time: List[int] = []
        self.history_pnl: List[int] = []
        self.history_inventory: List[int] = []
        self.active_order_ids: List[str] = []

    def pnl(self, mark_price: int) -> int:
        """Mark-to-market PnL = Cash + Inventory * MarkPrice."""
        return self.cash + (self.inventory * mark_price)

    def on_trade(self, side: Side, price: int, qty: int) -> None:
        """Update cash and inventory upon execution notification."""
        if side == Side.BUY:
            self.cash -= price * qty
            self.inventory += qty
        else:
            self.cash += price * qty
            self.inventory -= qty

    def record_snapshot(self, step: int, mark_price: int) -> None:
        self.history_time.append(step)
        self.history_pnl.append(self.pnl(mark_price))
        self.history_inventory.append(self.inventory)


class MarketMaker(BaseBot):
    """Quotes bids and asks around fair value and skews prices to manage inventory risk."""

    def __init__(self, bot_id: str, half_spread: int = 2, max_inventory: int = 50) -> None:
        super().__init__(bot_id)
        self.half_spread = half_spread
        self.max_inventory = max_inventory
        self.last_bid_id: Optional[str] = None
        self.last_ask_id: Optional[str] = None

    def decide(
        self,
        step: int,
        best_bid: Optional[int],
        best_ask: Optional[int],
        last_price: int,
    ) -> List[Tuple[str, Any]]:
        """Generate cancel and quote actions.

        Inventory Skew:
        - If long inventory (inventory > 0): lower bids and lower asks to unload inventory.
        - If short inventory (inventory < 0): raise bids and raise asks to cover.
        """
        actions = []
        mid = (best_bid + best_ask) // 2 if (best_bid and best_ask) else last_price

        # Inventory shade factor
        skew = int(self.inventory * 0.15)
        target_bid = max(1, mid - self.half_spread - skew)
        target_ask = max(target_bid + 1, mid + self.half_spread - skew)

        # Cancel previous quotes
        if self.last_bid_id:
            actions.append(("CANCEL", self.last_bid_id))
            self.last_bid_id = None
        if self.last_ask_id:
            actions.append(("CANCEL", self.last_ask_id))
            self.last_ask_id = None

        # Quote if within inventory limits
        quote_qty = 10
        if self.inventory < self.max_inventory:
            self.last_bid_id = f"{self.bot_id}_bid_{step}"
            actions.append(("LIMIT", self.last_bid_id, Side.BUY, quote_qty, target_bid))

        if self.inventory > -self.max_inventory:
            self.last_ask_id = f"{self.bot_id}_ask_{step}"
            actions.append(("LIMIT", self.last_ask_id, Side.SELL, quote_qty, target_ask))

        return actions


class NoiseTrader(BaseBot):
    """Places uncoordinated random limit and market orders around the market price."""

    def __init__(self, bot_id: str, rng: random.Random) -> None:
        super().__init__(bot_id)
        self.rng = rng

    def decide(
        self,
        step: int,
        best_bid: Optional[int],
        best_ask: Optional[int],
        last_price: int,
    ) -> List[Tuple[str, Any]]:
        actions = []
        p = self.rng.random()
        side = Side.BUY if self.rng.random() < 0.50 else Side.SELL
        qty = self.rng.randint(1, 15)

        if p < 0.25:
            # Aggressive market order
            oid = f"{self.bot_id}_mkt_{step}"
            actions.append(("MARKET", oid, side, qty))
        elif p < 0.70:
            # Passive limit order near the inside
            ref = (best_bid or last_price) if side == Side.BUY else (best_ask or last_price)
            offset = self.rng.randint(-3, 3)
            price = max(1, ref + offset)
            oid = f"{self.bot_id}_lmt_{step}"
            actions.append(("LIMIT", oid, side, qty, price))
            self.active_order_ids.append(oid)
        elif self.active_order_ids:
            # Cancel an older order
            oid = self.active_order_ids.pop(0)
            actions.append(("CANCEL", oid))

        return actions


class TrendFollower(BaseBot):
    """Momentum trader: buys when price rises above short moving average, sells when below."""

    def __init__(self, bot_id: str, lookback: int = 15) -> None:
        super().__init__(bot_id)
        self.lookback = lookback
        self.price_history: List[int] = []

    def observe_price(self, price: int) -> None:
        self.price_history.append(price)
        if len(self.price_history) > self.lookback * 2:
            self.price_history.pop(0)

    def decide(
        self,
        step: int,
        best_bid: Optional[int],
        best_ask: Optional[int],
        last_price: int,
    ) -> List[Tuple[str, Any]]:
        self.observe_price(last_price)
        if len(self.price_history) < self.lookback:
            return []

        recent_sma = sum(self.price_history[-self.lookback:]) / self.lookback
        actions = []

        # Buy when price crosses above SMA, Sell when below
        if last_price > recent_sma + 1.0 and self.inventory < 40:
            oid = f"{self.bot_id}_buy_{step}"
            actions.append(("MARKET", oid, Side.BUY, 5))
        elif last_price < recent_sma - 1.0 and self.inventory > -40:
            oid = f"{self.bot_id}_sell_{step}"
            actions.append(("MARKET", oid, Side.SELL, 5))

        return actions
