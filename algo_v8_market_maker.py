import json
import math
import collections
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for the Optimized Market Maker V8 Strategy."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Relaxed Thresholds for Edge-Based Quoting
    STRONG_THRESHOLD = 1.2 # Lowered from 1.5
    WEAK_THRESHOLD = 0.3   # Lowered from 0.5
    EDGE_SCALE = 2.5       
    
    # Fair Value & Microstructure
    MICRO_ADJUSTMENT_K = 0.8
    LEVELS_TO_CONSIDER = 5
    EPSILON = 1e-9
    
    # Spreads & Quoting
    BASE_SPREAD = {"TOMATOES": 4, "EMERALDS": 2}
    MIN_SPREAD = 2
    MAX_SPREAD = 40
    
    # Inventory Management
    LIQUIDATION_THRESHOLD = 0.8 # 80% of limit
    SKEW_FACTOR = 4.0
    
    # Alpha & Aggressive
    ALPHA_AGGR_VALUE_THRESHOLD = 1.5 # Lowered from 2.0
    MOMENTUM_AGGR_THRESHOLD = 0.5    # New threshold for trend-based aggression
    
    # Execution Size
    BASE_ORDER_SIZE = 25
    MIN_ORDER_SIZE = 3     # Size floor
    CONFIDENCE_FLOOR = 0.3 # Minimum confidence
    
    # Volatility Guard
    VOL_GUARD_THRESHOLD = 1.8
    VOL_PENALTY_FACTOR = 1.5
    VOL_SIZE_REDUCTION = 0.8 # Less aggressive reduction (kept mostly intact)

class StateManager:
    def __init__(self):
        self.mid_history: Dict[str, collections.deque] = {}
        self.vola_ema: Dict[str, float] = {}
        self.last_prices: Dict[str, float] = {}

    def update(self, product: str, mid: float):
        if product not in self.mid_history:
            self.mid_history[product] = collections.deque(maxlen=10)
            self.vola_ema[product] = 0.0
            self.last_prices[product] = mid
        self.mid_history[product].append(mid)
        diff = abs(mid - self.last_prices[product])
        self.vola_ema[product] = (0.1 * diff) + (0.9 * self.vola_ema[product])
        self.last_prices[product] = mid

    def get_momentum(self, product: str) -> float:
        history = self.mid_history.get(product)
        if not history or len(history) < 2: return 0.0
        return history[-1] - history[-2]

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
            
            # --- 1. Fair Price & Edge ---
            if product == "EMERALDS":
                fair = 10000.0
            else:
                fair = vwap + (Config.MICRO_ADJUSTMENT_K * imbalance)
            
            edge = fair - mid
            momentum = self.state.get_momentum(product)
            vol = self.state.vola_ema.get(product, 0.0)
            
            # --- 2. Dynamic Size & Confidence ---
            confidence = max(Config.CONFIDENCE_FLOOR, min(abs(edge) / Config.EDGE_SCALE, 1.0))
            raw_size = Config.BASE_ORDER_SIZE * confidence
            if vol > Config.VOL_GUARD_THRESHOLD:
                raw_size *= Config.VOL_SIZE_REDUCTION
            
            size = max(Config.MIN_ORDER_SIZE, int(round(raw_size)))
            
            # --- 3. Quoting Logic (Relaxed) ---
            bid_price, ask_price = self._get_quotes(product, edge, best_bid, best_ask, fair, pos, limit)
            
            # --- 4. Momentum Filtering (Soft) ---
            buy_size = min(limit - pos, size)
            sell_size = max(-(limit + pos), -size)
            
            if buy_size > 0 and momentum < -0.4:
                buy_size = max(Config.MIN_ORDER_SIZE, int(buy_size * 0.6))
            if sell_size < 0 and momentum > 0.4:
                sell_size = min(-Config.MIN_ORDER_SIZE, int(sell_size * 0.6))

            # --- 5. Hard Risk Control ---
            if abs(pos) >= limit * Config.LIQUIDATION_THRESHOLD:
                if pos > 0: orders.append(Order(product, best_bid, -pos))
                else: orders.append(Order(product, best_ask, abs(pos)))
                result[product] = orders
                continue

            # --- 6. Execution ---
            if buy_size > 0:
                orders.append(Order(product, bid_price, buy_size))
            if sell_size < 0:
                orders.append(Order(product, ask_price, sell_size))
                
            # --- 7. Aggressive Taker Logic (Relaxed) ---
            edge_vs_ask = fair - best_ask
            edge_vs_bid = best_bid - fair
            
            aggr_buy = edge_vs_ask > Config.ALPHA_AGGR_VALUE_THRESHOLD or (momentum > Config.MOMENTUM_AGGR_THRESHOLD and edge_vs_ask > 0)
            aggr_sell = edge_vs_bid > Config.ALPHA_AGGR_VALUE_THRESHOLD or (momentum < -Config.MOMENTUM_AGGR_THRESHOLD and edge_vs_bid > 0)
            
            if aggr_buy and (limit - pos) > 0:
                orders.append(Order(product, best_ask, min(limit - pos, 5)))
            if aggr_sell and (limit + pos) > 0:
                orders.append(Order(product, best_bid, -min(limit + pos, 5)))

            print(f"[{state.timestamp}] {product:10} | E:{edge:5.2f} | M:{momentum:5.2f} | P:{pos:4} | Sz:{size:2} | Decision: PASSIVE + AGGR={aggr_buy or aggr_sell}")
            result[product] = orders
            
        return result, conversions, state.traderData

    def _get_quotes(self, product, edge, best_bid, best_ask, fair, pos, limit) -> Tuple[int, int]:
        skew = -math.copysign((abs(pos) / limit) ** 1.2, pos) * Config.SKEW_FACTOR
        if product == "EMERALDS": skew = 0
        
        # Base bid
        if edge > Config.STRONG_THRESHOLD:
            bid = best_bid + 1
        elif edge > Config.WEAK_THRESHOLD:
            bid = best_bid
        else:
            bid = best_bid - 1
            
        # Base ask
        if edge < -Config.STRONG_THRESHOLD:
            ask = best_ask - 1
        elif edge < -Config.WEAK_THRESHOLD:
            ask = best_ask
        else:
            ask = best_ask + 1
            
        return int(bid + skew), int(ask + skew)

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
        imb = (b_vol - a_vol) / (b_vol + a_vol + Config.EPSILON)
        return vwap, imb
