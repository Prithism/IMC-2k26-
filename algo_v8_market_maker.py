import json
import math
import collections
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for Precision Burst Trader V10."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # High-Precision Thresholds
    EDGE_THRESHOLD = 0.5
    ATTACK_THRESHOLD = 2.0 # Only major mispricing
    IMBALANCE_THRESHOLD = 0.75
    TREND_THRESHOLD = 1.5
    
    # Burst Settings
    BURST_WINDOW = 3 
    COOL_DOWN = 10
    
    # Sizing
    BASE_SIZE = 10
    MICRO_MM_SIZE = 4
    
    # Fair Value
    MID_WEIGHT = 0.7
    VWAP_WEIGHT = 0.3

class StateManager:
    def __init__(self):
        self.mid_history: Dict[str, collections.deque] = {}
        self.attack_mode_ticks: Dict[str, int] = {}
        self.cool_down_ticks: Dict[str, int] = {}
        self.last_attack_dir: Dict[str, int] = {} 

    def update(self, product: str, mid: float):
        if product not in self.mid_history:
            self.mid_history[product] = collections.deque(maxlen=20)
            self.attack_mode_ticks[product] = 0
            self.cool_down_ticks[product] = 0
            self.last_attack_dir[product] = 0
        self.mid_history[product].append(mid)
        if self.attack_mode_ticks[product] > 0: self.attack_mode_ticks[product] -= 1
        if self.cool_down_ticks[product] > 0: self.cool_down_ticks[product] -= 1

    def get_trend(self, product: str) -> float:
        h = self.mid_history.get(product)
        return (h[-1] - h[-11]) if h and len(h) >= 11 else 0.0

    def trigger_attack(self, product: str, direction: int):
        if self.cool_down_ticks[product] == 0:
            self.attack_mode_ticks[product] = Config.BURST_WINDOW
            self.cool_down_ticks[product] = Config.COOL_DOWN
            self.last_attack_dir[product] = direction

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = Config.DEFAULT_LIMIT

    def __init__(self):
        self.state = StateManager()

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        
        for product, depth in state.order_depths.items():
            orders: List[Order] = []
            pos = state.position.get(product, 0)
            limit = Config.POSITION_LIMITS.get(product, Config.DEFAULT_LIMIT)
            
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid is None or best_ask is None: continue
            
            mid = (best_bid + best_ask) / 2.0
            self.state.update(product, mid)
            
            # --- 1. Analysis ---
            vwap, b_vol, a_vol = self._get_ob_stats(depth)
            imbalance = (b_vol - a_vol) / (b_vol + a_vol + 1e-9)
            trend = self.state.get_trend(product)
            fair = 10000.0 if product == "EMERALDS" else (Config.MID_WEIGHT * mid) + (Config.VWAP_WEIGHT * vwap)
            edge = fair - mid
            
            # --- 2. Confluence Trigger ---
            attack_buy = edge > Config.ATTACK_THRESHOLD and (imbalance > Config.IMBALANCE_THRESHOLD or trend > Config.TREND_THRESHOLD)
            attack_sell = edge < -Config.ATTACK_THRESHOLD and (imbalance < -Config.IMBALANCE_THRESHOLD or trend < -Config.TREND_THRESHOLD)
            
            if attack_buy: self.state.trigger_attack(product, 1)
            elif attack_sell: self.state.trigger_attack(product, -1)
            
            in_attack = self.state.attack_mode_ticks[product] > 0
            attack_dir = self.state.last_attack_dir[product]
            
            # --- 3. Execution ---
            can_buy = limit - pos
            can_sell = -(limit + pos)
            
            if in_attack:
                size = Config.BASE_SIZE * 3
                if attack_dir == 1 and edge > 0 and pos < limit * 0.5:
                    orders.append(Order(product, best_ask, min(can_buy, size)))
                elif attack_dir == -1 and edge < 0 and pos > -limit * 0.5:
                    orders.append(Order(product, best_bid, max(can_sell, -size)))
            
            # --- 4. Quality Market Making ---
            # Bid side
            if edge > Config.EDGE_THRESHOLD and pos < limit:
                bid_px = best_bid if edge < 1.0 else best_bid + 1
                orders.append(Order(product, bid_px, min(can_buy, Config.BASE_SIZE)))
            else: # Micro MM
                orders.append(Order(product, best_bid - 1, Config.MICRO_MM_SIZE))
                
            # Ask side
            if edge < -Config.EDGE_THRESHOLD and pos > -limit:
                ask_px = best_ask if edge > -1.0 else best_ask - 1
                orders.append(Order(product, ask_px, max(can_sell, -Config.BASE_SIZE)))
            else: # Micro MM
                orders.append(Order(product, best_ask + 1, -Config.MICRO_MM_SIZE))

            print(f"[{state.timestamp}] {product:10} | Edge:{edge:5.2f} | Pos:{pos:4} | Burst:{in_attack}")
            result[product] = orders
            
        return result, conversions, state.traderData

    def _get_ob_stats(self, depth: OrderDepth) -> Tuple[float, float, float]:
        b = sorted(depth.buy_orders.items(), reverse=True)[:3]
        s = sorted(depth.sell_orders.items())[:3]
        if not b or not s: return 0.0, 0.0, 0.0
        t_vol = 0; t_val = 0; b_vol = 0; a_vol = 0
        for p, v in b: t_val += p * v; t_vol += v; b_vol += v
        for p, v in s: av = abs(v); t_val += p * av; t_vol += av; a_vol += av
        return t_val / t_vol if t_vol > 0 else 0.0, b_vol, a_vol
