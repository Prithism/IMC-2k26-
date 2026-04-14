import csv
import contextlib
import io
import sys
import collections
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from datamodel import Listing, Order, OrderDepth, TradingState

import importlib

# ---------------------------------------------------------------------------
# BACKTESTER LOGIC
# ---------------------------------------------------------------------------
DATA_DIR = Path(r"c:\Users\prith\Downloads\TUTORIAL_ROUND_1")
CSV_FILES = ["prices_round_0_day_-2.csv", "prices_round_0_day_-1.csv"]

def build_depth(row: dict) -> OrderDepth:
    d = OrderDepth()
    for l in range(1, 4):
        bp = row.get(f"bid_price_{l}")
        bv = row.get(f"bid_volume_{l}")
        ap = row.get(f"ask_price_{l}")
        av = row.get(f"ask_volume_{l}")
        if bp: d.buy_orders[int(float(bp))] = int(float(bv))
        if ap: d.sell_orders[int(float(ap))] = -int(float(av))
    return d

def run_backtest(algo_module_name: str):
    # Dynamically import the Trader class
    try:
        module = importlib.import_module(algo_module_name)
        TraderClass = getattr(module, "Trader")
    except Exception as e:
        print(f"Error: Could not import Trader from {algo_module_name}. {e}")
        return

    trader = TraderClass()
    positions = {}
    cash = {}
    ticks = 0
    last_mid = {}

    print(f"\nStarting Backtest for: {algo_module_name}")

    for f in CSV_FILES:
        p = DATA_DIR / f
        if not p.exists(): continue
        with open(p, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter=";")
            for row in reader:
                ticks += 1
                prod = row["product"]
                ts = int(row["timestamp"])
                mid = float(row["mid_price"]) if row["mid_price"] else 0
                last_mid[prod] = mid
                
                state = TradingState(
                    traderData="",
                    timestamp=ts,
                    listings={prod: Listing(prod, prod, "")},
                    order_depths={prod: build_depth(row)},
                    own_trades={prod: []},
                    market_trades={prod: []},
                    position=dict(positions),
                    observations={}
                )
                
                res, _, _ = trader.run(state)
                orders = res.get(prod, [])
                
                # Simple Fill logic
                limit = trader.POSITION_LIMITS.get(prod, getattr(trader, 'DEFAULT_POSITION_LIMIT', 20))
                curr = positions.get(prod, 0)
                
                for o in orders:
                    qty = o.quantity
                    if qty > 0: qty = min(qty, limit - curr)
                    else: qty = max(qty, -(curr + limit))
                    
                    if qty == 0: continue
                    
                    price = o.price
                    curr += qty
                    positions[prod] = curr
                    cash[prod] = cash.get(prod, 0.0) - (qty * price)
                    
    print("\n" + "="*50)
    print(f"  BACKTEST RESULTS ({algo_module_name})")
    print("="*50)
    final_pnl = 0
    for prod in set(list(positions.keys()) + list(last_mid.keys())):
        p_cash = cash.get(prod, 0.0)
        p_pos = positions.get(prod, 0)
        p_mtm = p_pos * last_mid.get(prod, 0)
        p_pnl = p_cash + p_mtm
        final_pnl += p_pnl
        print(f"  {prod:<12} | PnL: {p_pnl:>10,.1f} | Pos: {p_pos}")
    
    print("-" * 50)
    print(f"  TOTAL PnL: {final_pnl:,.1f}")
    print("=" * 50)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Default back to algo2_srijit if no arg
        algo = "algo2_srijit"
    else:
        algo = sys.argv[1]
        if algo.endswith(".py"):
            algo = algo[:-3]
    
    run_backtest(algo)
