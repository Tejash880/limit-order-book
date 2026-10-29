"""Agent-based market simulation runner."""

from __future__ import annotations
import argparse
import random
import sys
from pathlib import Path

# Ensure 'src' and project root are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from typing import Dict, List, Tuple
from lob.engine import MatchingEngine
from lob.models import Side, OrderType, TradeEvent
from sim.bots import BaseBot, MarketMaker, NoiseTrader, TrendFollower
from sim.plots import plot_simulation_results


def run_simulation(
    num_steps: int = 5000,
    seed: int = 42,
    output_dir: Path = Path("sim/output"),
) -> Tuple[MatchingEngine, Dict[str, BaseBot], List[Tuple[int, int]]]:
    """Execute reproducible agent-based simulation."""
    rng = random.Random(seed)
    engine = MatchingEngine()

    mm = MarketMaker("MM", half_spread=2, max_inventory=40)
    noise = NoiseTrader("Noise", rng=random.Random(seed + 1))
    trend = TrendFollower("Trend", lookback=20)

    bots: Dict[str, BaseBot] = {
        "MM": mm,
        "Noise": noise,
        "Trend": trend,
    }

    def get_bot_for_order(order_id: str) -> BaseBot:
        prefix = order_id.split("_")[0]
        return bots[prefix]

    trade_history: List[Tuple[int, int]] = []
    last_price = 100

    # Seed the book with initial market maker quotes
    for action in mm.decide(0, None, None, last_price):
        if action[0] == "LIMIT":
            engine.process_order(action[1], action[2], OrderType.LIMIT, action[3], action[4])

    for step in range(1, num_steps + 1):
        bb = engine.best_bid
        ba = engine.best_ask

        # 1. Gather decisions from each bot in randomized sequence
        turn_order = [mm, noise, trend]
        rng.shuffle(turn_order)

        for bot in turn_order:
            actions = bot.decide(step, bb, ba, last_price)
            for act in actions:
                kind = act[0]
                events = []
                if kind == "LIMIT":
                    events = engine.process_order(act[1], act[2], OrderType.LIMIT, act[3], act[4])
                elif kind == "MARKET":
                    events = engine.process_order(act[1], act[2], OrderType.MARKET, act[3])
                elif kind == "CANCEL":
                    events = engine.cancel_order(act[1])

                # Process fill events and notify participants
                for ev in events:
                    if isinstance(ev, TradeEvent):
                        last_price = ev.price
                        trade_history.append((step, ev.price))

                        maker_bot = get_bot_for_order(ev.maker_order_id)
                        taker_bot = get_bot_for_order(ev.taker_order_id)

                        maker_bot.on_trade(ev.maker_side, ev.price, ev.quantity)
                        taker_bot.on_trade(ev.maker_side.opposite, ev.price, ev.quantity)

        # 2. Record periodic snapshot
        if step % 5 == 0:
            mark = last_price
            for bot in bots.values():
                bot.record_snapshot(step, mark)

    # 3. Generate charts
    plot_simulation_results(trade_history, bots, output_dir)
    return engine, bots, trade_history


def main():
    parser = argparse.ArgumentParser(description="LOB Agent Simulator")
    parser.add_argument("--steps", type=int, default=5000, help="Simulation steps")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--output", type=str, default="sim/output", help="Output directory")
    args = parser.parse_args()

    out_path = Path(args.output)
    print(f"Running simulation ({args.steps} steps, seed={args.seed})...")
    engine, bots, trades = run_simulation(num_steps=args.steps, seed=args.seed, output_dir=out_path)

    print("\n" + "=" * 50)
    print("SIMULATION SUMMARY")
    print("=" * 50)
    print(f"Total Steps       : {args.steps:,}")
    print(f"Total Trades      : {len(trades):,}")
    print(f"Final Best Bid    : {engine.best_bid}")
    print(f"Final Best Ask    : {engine.best_ask}")
    print("-" * 50)
    for bot_id, bot in bots.items():
        mark = trades[-1][1] if trades else 100
        pnl = bot.pnl(mark) - bot.history_pnl[0]
        print(f"[{bot_id:5s}] Relative PnL: {pnl:+8d} ticks | Net Inventory: {bot.inventory:+4d} lots")
    print("=" * 50)
    print(f"Charts saved to: {out_path.resolve()}/")


if __name__ == "__main__":
    main()
