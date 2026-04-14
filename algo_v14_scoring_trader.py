import collections
import math
from typing import Dict, List, Tuple
from datamodel import Order, OrderDepth, TradingState

class Config:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Thresholds
    EDGE_MIN = 0.2
    ATTACK_EDGE = 0.8
    IMBALANCE_TH = 0.3
    TREND_TH = 0.5
    
    # Risk
    PROFIT_LOCK_DD = 200.0
    VOLA_EMA_ALPHA = 0.1
    
    # Sizing
    BASE_SIZE = 10
    MICRO_SIZE = 2

class StateManager:
    def __init__(self):
        self.mid_history = {}
        self.vola_ema = {}
        self.peak_pnl = 0.0

    def update(self, prod: str, mid: float):
        if prod not in self.mid_history:
            self.mid_history[prod] = collections.deque(maxlen=20)
            self.vola_ema[prod] = 0.0
        
        last_mid = self.mid_history[prod][-1] if self.mid_history[prod] else mid
        self.vola_ema[prod] = Config.VOLA_EMA_ALPHA * abs(mid - last_mid) + (1.0 - Config.VOLA_EMA_ALPHA) * self.vola_ema[prod]
        self.mid_history[prod].append(mid)

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
            
            # --- 1. Signal Collection ---
            vwap, b_vol, a_vol = self._get_ob_stats(depth)
            imb = (b_vol - a_vol) / (b_vol + a_vol + 1e-9)
            fair = (0.7 * mid + 0.3 * vwap) if prod != "EMERALDS" else 10000.0
            
            h = self.state.mid_history[prod]
            trend = (h[-1] - h[-11]) if len(h) >= 11 else 0.0
            micro_trend = (h[-1] - h[-4]) if len(h) >= 4 else 0.0
            vol = self.state.vola_ema[prod]
            
            # --- 2. Scoring System ---
            # BUY SCORE
            score_b = 0
            if (fair - best_ask) > Config.EDGE_MIN: score_b += 1
            if imb > Config.IMBALANCE_TH: score_b += 1
            if trend > Config.TREND_TH: score_b += 1
            if micro_trend > 0: score_b += 1
            if (fair - best_ask) > Config.ATTACK_EDGE: score_b = 4 # Force Attack
            
            # SELL SCORE
            score_s = 0
            if (best_bid - fair) > Config.EDGE_MIN: score_s += 1
            if imb < -Config.IMBALANCE_TH: score_s += 1
            if trend < -Config.TREND_TH: score_s += 1
            if micro_trend < 0: score_s += 1
            if (best_bid - fair) > Config.ATTACK_EDGE: score_s = 4 # Force Attack
            
            # --- 3. Dynamic Controls ---
            size_raw = Config.BASE_SIZE
            if vol > 2.0: size_raw *= 0.7 # Vol reduction
            if abs(pos) > limit * 0.7: size_raw *= 0.5 # Position reduction
            
            can_b = limit - pos
            can_s = -(limit + pos)
            
            # --- 4. Trade Execution ---
            # ALWAYS-ON Micro-MM
            orders.append(Order(prod, best_bid - 1, Config.MICRO_SIZE))
            orders.append(Order(prod, best_ask + 1, -Config.MICRO_SIZE))
            
            # SCORE-BASED Execution
            # Buy Side
            if score_b >= 3 and can_b > 0:
                orders.append(Order(prod, best_ask, min(can_b, int(size_raw * 1.5))))
            elif score_b >= 2 and can_b > 0:
                orders.append(Order(prod, best_bid, min(can_b, int(size_raw))))
                
            # Sell Side
            if score_s >= 3 and can_s < 0:
                orders.append(Order(prod, best_bid, max(can_s, -int(size_raw * 1.5))))
            elif score_s >= 2 and can_s < 0:
                orders.append(Order(prod, best_ask, max(can_s, -int(size_raw))))

            # --- 5. Inventory Rebalancing ---
            if abs(pos) > limit * 0.9: # Emergency reduction
                if pos > 0: orders.append(Order(prod, best_bid, -pos))
                else: orders.append(Order(prod, best_ask, abs(pos)))

            result[prod] = orders

        return result, 0, state.traderData

    def _get_ob_stats(self, depth: OrderDepth) -> Tuple[float, float, float]:
        b = sorted(depth.buy_orders.items(), reverse=True)[:3]
        s = sorted(depth.sell_orders.items())[:3]
        if not b or not s: return 0.0, 0.0, 0.0
        v_sum = 0; p_v_sum = 0; bv = 0; sv = 0
        for p,v in b: p_v_sum += p*v; v_sum += v; bv += v
        for p,v in s: av = abs(v); p_v_sum += p*av; v_sum += av; sv += av
        return p_v_sum / v_sum if v_sum > 0 else 0.0, bv, sv
