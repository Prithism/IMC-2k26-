import collections
import math
from typing import Dict, List, Tuple
from datamodel import Order, OrderDepth, TradingState

class Config:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Ultra-Selective Stealth Thresholds
    # We ignore anything less than 2.0 ticks of edge
    MANDATORY_EDGE = 2.0   
    IMBALANCE_FILTER = 0.70 # Strong 3:1 ratio pressure
    
    # Pricing
    MID_WEIGHT = 0.8
    VWAP_WEIGHT = 0.2
    
    # Safety
    LIQUIDATION_THRESHOLD = 0.35 # Even tighter (35%)
    SKEW_FACTOR = 10.0 # Extreme pressure to stay at 0
    COOL_DOWN_TICKS = 15

class StateManager:
    def __init__(self):
        self.mid_history = {}
        self.cool_down = {}

    def update(self, prod: str, mid: float):
        if prod not in self.mid_history:
            self.mid_history[prod] = collections.deque(maxlen=20)
            self.cool_down[prod] = 0
        self.mid_history[prod].append(mid)
        if self.cool_down[prod] > 0: self.cool_down[prod] -= 1

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = 20

    def __init__(self):
        self.state = StateManager()

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        
        for prod, depth in state.order_depths.items():
            orders = []
            pos = state.position.get(prod, 0)
            limit = self.POSITION_LIMITS.get(prod, Config.DEFAULT_LIMIT)
            
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if not best_bid or not best_ask: continue
            
            mid = (best_bid + best_ask) / 2.0
            self.state.update(prod, mid)
            
            # --- 1. Selective Signal Check ---
            h = self.state.mid_history[prod]
            trend = h[-1] - h[-11] if len(h) >= 11 else 0.0
            
            vwap, b_vol, a_vol = self._get_ob_stats(depth)
            imb = (b_vol - a_vol) / (b_vol + a_vol + 1e-9)
            
            fair = (Config.MID_WEIGHT * mid + Config.VWAP_WEIGHT * vwap) if prod != "EMERALDS" else 10000.0
            
            # Stealth Edge calculation
            edge_buy = fair - best_ask
            edge_sell = best_bid - fair
            
            # --- 2. Predator Filters ---
            # 1. Edge must be massive
            # 2. Imbalance must confirm
            # 3. Trend must not be against us
            
            can_trade_buy = edge_buy > Config.MANDATORY_EDGE and imb > Config.IMBALANCE_FILTER and trend > 0
            can_trade_sell = edge_sell > Config.MANDATORY_EDGE and imb < -Config.IMBALANCE_FILTER and trend < 0
            
            if prod == "EMERALDS": # Pure mean reversion for Emeralds but selective
                can_trade_buy = (fair - best_bid) > 2.0
                can_trade_sell = (best_ask - fair) > 2.0

            # --- 3. Execution (Stealth Shadowing) ---
            # We sit 1 tick BEHIND the best prices to avoid toxic flow
            bid_px = best_bid - 1
            ask_px = best_ask + 1
            
            # Extreme Skew
            skew = -int(math.copysign((pos / limit) ** 2 * Config.SKEW_FACTOR, pos))
            bid_px += skew; ask_px += skew
            
            can_b = limit - pos
            can_s = -(limit + pos)

            # Defensive Market Making (Only when highly profitable)
            if can_trade_buy and can_b > 0:
                orders.append(Order(prod, int(bid_px), min(can_b, 10)))
            if can_trade_sell and can_s < 0:
                orders.append(Order(prod, int(ask_px), max(can_s, -10)))
                
            # --- 4. Emergency Flush ---
            if abs(pos) > limit * Config.LIQUIDATION_THRESHOLD:
                orders = []
                if pos > 0: orders.append(Order(prod, best_bid, -pos))
                else: orders.append(Order(prod, best_ask, abs(pos)))

            result[prod] = orders

        return result, 0, state.traderData

    def _get_ob_stats(self, depth: OrderDepth) -> Tuple[float, float, float]:
        b = sorted(depth.buy_orders.items(), reverse=True)[:3]
        s = sorted(depth.sell_orders.items())[:3]
        if not b or not s: return 0.0, 0.0, 0.0
        t_val = 0; t_vol = 0; bv = 0; sv = 0
        for p, v in b: t_val += p * v; t_vol += v; bv += v
        for p, v in s: av = abs(v); t_val += p * av; t_vol += av; sv += av
        return t_val / t_vol if t_vol > 0 else 0.0, bv, sv
