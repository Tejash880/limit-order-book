"""Visualization module for simulation results using Matplotlib."""

from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
import matplotlib.pyplot as plt
from sim.bots import BaseBot


def plot_simulation_results(
    trade_history: List[Tuple[int, int]],  # (step, trade_price)
    bots: Dict[str, BaseBot],
    output_dir: Path,
) -> Tuple[Path, Path, Path]:
    """Generate and save the three required simulation charts.

    Returns:
        Tuple of (path_trade_price, path_bot_pnl, path_bot_inventory)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")

    # 1. Trade Price Over Time
    fig_price, ax_price = plt.subplots(figsize=(10, 5), dpi=150)
    if trade_history:
        steps, prices = zip(*trade_history)
        ax_price.plot(steps, prices, color="#1f77b4", linewidth=1.2, label="Trade Execution Price")
    ax_price.set_title("Market Dynamics: Trade Price Over Time", fontsize=14, fontweight="bold")
    ax_price.set_xlabel("Simulation Step", fontsize=11)
    ax_price.set_ylabel("Price (Ticks)", fontsize=11)
    ax_price.legend(loc="upper left")
    price_path = output_dir / "trade_price.png"
    fig_price.tight_layout()
    fig_price.savefig(price_path)
    plt.close(fig_price)

    # 2. Bot Profit and Loss (PnL) Over Time
    fig_pnl, ax_pnl = plt.subplots(figsize=(10, 5), dpi=150)
    colors = {"MM": "#2ca02c", "Noise": "#d62728", "Trend": "#9467bd"}
    for bot_id, bot in bots.items():
        color = colors.get(bot_id, None)
        # Normalize PnL so initial cash is baseline 0
        baseline = bot.history_pnl[0] if bot.history_pnl else 0
        relative_pnl = [p - baseline for p in bot.history_pnl]
        ax_pnl.plot(bot.history_time, relative_pnl, label=f"{bot_id} (PnL)", linewidth=1.5, color=color)

    ax_pnl.axhline(0, color="black", linestyle="--", alpha=0.6, linewidth=0.8)
    ax_pnl.set_title("Agent Performance: Mark-to-Market PnL Over Time", fontsize=14, fontweight="bold")
    ax_pnl.set_xlabel("Simulation Step", fontsize=11)
    ax_pnl.set_ylabel("Relative PnL (Ticks)", fontsize=11)
    ax_pnl.legend(loc="upper left")
    pnl_path = output_dir / "bot_pnl.png"
    fig_pnl.tight_layout()
    fig_pnl.savefig(pnl_path)
    plt.close(fig_pnl)

    # 3. Bot Inventory Over Time
    fig_inv, ax_inv = plt.subplots(figsize=(10, 5), dpi=150)
    for bot_id, bot in bots.items():
        color = colors.get(bot_id, None)
        ax_inv.plot(bot.history_time, bot.history_inventory, label=f"{bot_id} Inventory", linewidth=1.3, color=color)

    ax_inv.axhline(0, color="black", linestyle="--", alpha=0.6, linewidth=0.8)
    ax_inv.set_title("Agent Risk: Net Inventory Position Over Time", fontsize=14, fontweight="bold")
    ax_inv.set_xlabel("Simulation Step", fontsize=11)
    ax_inv.set_ylabel("Inventory (Lots)", fontsize=11)
    ax_inv.legend(loc="upper left")
    inv_path = output_dir / "bot_inventory.png"
    fig_inv.tight_layout()
    fig_inv.savefig(inv_path)
    plt.close(fig_inv)

    return price_path, pnl_path, inv_path
