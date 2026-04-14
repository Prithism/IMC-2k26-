import json
import math
import collections
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for Dual-Mode Burst Trader V10."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Thresholds
    EDGE_THRESHOLD = 0.3
    ATTACK_THRESHOLD = 1.0
    IMBALANCE_THRESHOLD = 0.6
    TREND_THRESHOLD = 0.8
    
    # Burst Settings
    BURST_WINDOW = 4 # Ticks to stay in Attack Mode
    
    # Sizing
    BASE_SIZE = 5
    MICRO_MM_SIZE = 2
    
    # Fair Value
    MID_WEIGHT = 0.7
    VWAP_WEIGHT = 0.3

class StateManager:
    def __init__(self):
        self.mid_history: Dict[str, collections.deque] = {}
        self.attack_mode_ticks: Dict[str, int] = {}
        self.last_attack_dir: Dict[str, int] = {} # 1 for buy, -1 for sell

    def update(self, product: str, mid: float):
        if product not in self.mid_history:
            self.mid_history[product] = collections.deque(maxlen=20)
            self.attack_mode_ticks[product] = 0
            self.last_attack_dir[product] = 0
        self.mid_history[product].append(mid)
        if self.attack_mode_ticks[product] > 0:
            self.attack_mode_ticks[product] -= 1

    def get_trend(self, product: str) -> float:
        history = self.mid_history.get(product)
        if not history or len(history) < 6: return 0.0
        return history[-1] - history[-6]

    def trigger_attack(self, product: str, direction: int):
        self.attack_mode_ticks[product] = Config.BURST_WINDOW
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
            
            # --- 1. Fair Price & Signals ---
            vwap, b_vol, a_vol = self._get_ob_stats(depth)
            imbalance = (b_vol - a_vol) / (b_vol + a_vol + 1e-9)
            trend = self.state.get_trend(product)
            
            if product == "EMERALDS":
                fair = 10000.0
            else:
                fair = (Config.MID_WEIGHT * mid) + (Config.VWAP_WEIGHT * vwap)
            
            edge = fair - mid
            
            # --- 2. Attack Trigger Logic ---
            attack_buy = edge > Config.ATTACK_THRESHOLD or imbalance > Config.IMBALANCE_THRESHOLD or trend > Config.TREND_THRESHOLD
            attack_sell = edge < -Config.ATTACK_THRESHOLD or imbalance < -Config.IMBALANCE_THRESHOLD or trend < -Config.TREND_THRESHOLD
            
            if attack_buy: self.state.trigger_attack(product, 1)
            elif attack_sell: self.state.trigger_attack(product, -1)
            
            in_attack = self.state.attack_mode_ticks[product] > 0
            attack_dir = self.state.last_attack_dir[product]
            
            # --- 3. Dynamic Sizing ---
            abs_edge = abs(edge)
            if abs_edge > 1.0: mult = 3
            elif abs_edge > 0.5: mult = 2
            else: mult = 1
            
            size = Config.BASE_SIZE * mult
            if in_attack: size *= 3 # Attack multiplier
            
            # --- 4. Execution Mode ---
            if in_attack:
                # REFINED ATTACK MODE: Only fire if edge is aligned and position is safe
                if attack_dir == 1 and edge > -0.1: # Allow slight negative for trend chase
                    if pos < limit * 0.8:
                        qty = min(limit - pos, size)
                        if qty > 0: orders.append(Order(product, best_ask, qty))
                elif attack_dir == -1 and edge < 0.1:
                    if pos > -limit * 0.8:
                        qty = min(limit + pos, size)
                        if qty > 0: orders.append(Order(product, best_bid, -qty))
            
            # 5. PASSIVE MODE & MICRO MM
            # Always place small passive orders around fair price but favor flattening if near limits
            if (limit - pos) > 0:
                # If high pos, move bid deeper
                bid_adj = 2 if pos > limit * 0.7 else 1
                bid_px = min(best_bid, int(math.floor(fair - bid_adj)))
                passive_qty = Config.MICRO_MM_SIZE if not in_attack else 2
                orders.append(Order(product, bid_px, passive_qty))
                
            if (limit + pos) > 0:
                # If low pos, move ask deeper
                ask_adj = 2 if pos < -limit * 0.7 else 1
                ask_px = max(best_ask, int(math.ceil(fair + ask_adj)))
                passive_qty = Config.MICRO_MM_SIZE if not in_attack else 2
                orders.append(Order(product, ask_px, -passive_qty))

            # 6. Additional Passive for EDGE
            if edge > Config.EDGE_THRESHOLD and not in_attack:
                qty = min(limit - pos, size)
                if qty > 0: orders.append(Order(product, best_bid, qty))
            elif edge < -Config.EDGE_THRESHOLD and not in_attack:
                qty = min(limit + pos, size)
                if qty > 0: orders.append(Order(product, best_ask, -qty))

            mode_str = f"ATTACK({attack_dir})" if in_attack else "PASSIVE"
            print(f"[{state.timestamp}] {product:10} | {mode_str:10} | Edge:{edge:5.2f} | Imb:{imbalance:5.2f} | Pos:{pos:4}")
            result[product] = orders
            
        return result, conversions, state.traderData

    def _get_ob_stats(self, depth: OrderDepth) -> Tuple[float, float, float]:
        b = sorted(depth.buy_orders.items(), reverse=True)[:3]
        s = sorted(depth.sell_orders.items())[:3]
        if not b or not s: return 0.0, 0.0, 0.0
        t_vol = 0; t_val = 0; b_vol = 0; a_vol = 0
        for p, v in b: t_val += p * v; t_vol += v; b_vol += v
        for p, v in s: av = abs(v); t_val += p * av; t_vol += av; a_vol += av
        vwap = t_val / t_vol if t_vol > 0 else 0.0
        return vwap, b_vol, a_vol
