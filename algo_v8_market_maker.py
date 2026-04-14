import json
import math
import collections
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for Fixed Market Maker V8 (Stabilized)."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Thresholds for Edge-Based Quoting
    STRONG_THRESHOLD = 1.0 
    WEAK_THRESHOLD = 0.2
    EDGE_SCALE = 2.0       
    
    # Fair Value & Microstructure
    MICRO_ADJUSTMENT_K = 0.8
    LEVELS_TO_CONSIDER = 5
    EPSILON = 1e-9
    
    # Spreads & Quoting
    BASE_SPREAD = {"TOMATOES": 4, "EMERALDS": 2}
    
    # Regime & Trend (NERFED)
    TREND_THRESHOLD = 1.5           
    TREND_WEIGHT_NERFED = 0.2       # Reduced from 0.4
    
    # Inventory Management (STRENGTHENED)
    LIQUIDATION_THRESHOLD = 0.5     # 50% limit - more aggressive flattening
    SKEW_FACTOR = 8.0               # Doubled from 4.0
    
    # Aggression Thresholds
    ALPHA_AGGR_THRESHOLD = 1.5
    SEMI_AGGR_TREND_THRESHOLD = 1.0
    
    # Execution Size
    BASE_ORDER_SIZE = 30
    MIN_ORDER_SIZE = 5
    CONFIDENCE_FLOOR = 0.3

class StateManager:
    def __init__(self):
        self.mid_history: Dict[str, collections.deque] = {}
        self.vola_ema: Dict[str, float] = {}
        self.last_prices: Dict[str, float] = {}

    def update(self, product: str, mid: float):
        if product not in self.mid_history:
            self.mid_history[product] = collections.deque(maxlen=20)
            self.vola_ema[product] = 0.0
            self.last_prices[product] = mid
        self.mid_history[product].append(mid)
        diff = abs(mid - self.last_prices[product])
        self.vola_ema[product] = (0.1 * diff) + (0.9 * self.vola_ema[product])
        self.last_prices[product] = mid

    def get_trend(self, product: str) -> float:
        history = self.mid_history.get(product)
        if not history or len(history) < 6: return 0.0
        return history[-1] - history[-6]

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
            
            vwap, imbalance = self._get_ob_stats(depth)
            
            # --- 1. Fair Price & Limited Alpha ---
            if product == "EMERALDS":
                fair = 10000.0
            else:
                fair = vwap + (Config.MICRO_ADJUSTMENT_K * imbalance)
            
            # NERFED Trend Influence
            trend = self.state.get_trend(product)
            enhanced_edge = (fair - mid) + (Config.TREND_WEIGHT_NERFED * trend)
            
            # --- 2. Sizing ---
            vol = self.state.vola_ema.get(product, 0.0)
            confidence = max(Config.CONFIDENCE_FLOOR, min(abs(enhanced_edge) / Config.EDGE_SCALE, 1.0))
            size = max(Config.MIN_ORDER_SIZE, int(round(Config.BASE_ORDER_SIZE * confidence)))
            
            # --- 3. Strengthened Skew Logic ---
            # Power of 2 skew for faster flattening
            skew = -math.copysign((pos / limit) ** 2, pos) * Config.SKEW_FACTOR
            if product == "EMERALDS": skew = 0
            
            # --- 4. Quoting Engine (Core MM) ---
            if enhanced_edge > Config.STRONG_THRESHOLD:   bid_p = best_bid + 1
            elif enhanced_edge > Config.WEAK_THRESHOLD:   bid_p = best_bid
            else:                                         bid_p = best_bid - 1
                
            if enhanced_edge < -Config.STRONG_THRESHOLD:  ask_p = best_ask - 1
            elif enhanced_edge < -Config.WEAK_THRESHOLD:  ask_p = best_ask
            else:                                         ask_p = best_ask + 1

            # Apply Inventory Skew
            bid_p += int(math.floor(skew))
            ask_p += int(math.ceil(skew))

            # --- 5. Hard Position Control (FLATTEN) ---
            if abs(pos) >= limit * Config.LIQUIDATION_THRESHOLD:
                print(f"[{product}] FLATTENING: {pos} exceeds limit threshold.")
                if pos > 0: orders.append(Order(product, best_bid, -pos))
                else: orders.append(Order(product, best_ask, abs(pos)))
                result[product] = orders
                continue

            # Core Passive MM Orders
            can_buy = limit - pos
            can_sell = -(limit + pos)
            
            if can_buy > 0: orders.append(Order(product, bid_p, min(can_buy, size)))
            if can_sell < 0: orders.append(Order(product, ask_p, max(can_sell, -size)))
                
            # --- 6. Controlled Aggression ---
            # Use trend only for deciding aggression tier
            abs_trend = abs(trend)
            is_strong_trend = abs_trend > Config.SEMI_AGGR_TREND_THRESHOLD
            
            # Aggressive trades only if there's real value
            edge_only = fair - mid
            if (edge_only > Config.ALPHA_AGGR_THRESHOLD) or (is_strong_trend and edge_only > 0.5):
                t_qty = min(can_buy // 2, 8)
                if t_qty > 0: orders.append(Order(product, best_ask, t_qty))
            elif (edge_only < -Config.ALPHA_AGGR_THRESHOLD) or (is_strong_trend and edge_only < -0.5):
                t_qty = min(abs(can_sell) // 2, 8)
                if t_qty > 0: orders.append(Order(product, best_bid, -t_qty))

            result[product] = orders
            
        return result, conversions, state.traderData

    def _get_ob_stats(self, depth: OrderDepth) -> Tuple[float, float]:
        b = sorted(depth.buy_orders.items(), reverse=True)[:Config.LEVELS_TO_CONSIDER]
        s = sorted(depth.sell_orders.items())[:Config.LEVELS_TO_CONSIDER]
        if not b or not s: return 0.0, 0.0
        t_vol = 0; t_val = 0; b_vol = 0; a_vol = 0
        for p, v in b:
            t_val += p * v; t_vol += v; b_vol += v
        for p, v in s:
            av = abs(v); t_val += p * av; t_vol += av; a_vol += av
        vwap = t_val / t_vol if t_vol > 0 else 0.0
        imb = (b_vol - a_vol) / (b_vol+a_vol+Config.EPSILON)
        return vwap, imb
