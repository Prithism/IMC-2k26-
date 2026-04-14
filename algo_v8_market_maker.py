import json
import math
import collections
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for the Market Maker V8 Strategy."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Thresholds for Edge-Based Quoting
    STRONG_THRESHOLD = 1.5 # Ticks
    WEAK_THRESHOLD = 0.5   # Ticks
    EDGE_SCALE = 3.0       # Scale factor for confidence sizing
    
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
    ALPHA_AGGR_VALUE_THRESHOLD = 2.0 # Minimum real value edge to cross spread
    
    # Lead-Lag Alpha
    CORR_WINDOW = 10
    CORR_THRESHOLD = 0.00005
    LEAD_LAG_WEIGHT = 0.4
    
    # Volatility Guard
    VOLA_EMA_ALPHA = 0.1
    VOLA_GUARD_THRESHOLD = 1.5
    VOLA_PENALTY_FACTOR = 2.0
    SIZE_REDUCTION_FACTOR = 0.5
    
    # Execution Size
    BASE_ORDER_SIZE = 20

class StateManager:
    """Tracks historical data, momentum, and execution metrics."""
    def __init__(self):
        self.mid_history: Dict[str, collections.deque] = {}
        self.vola_ema: Dict[str, float] = {}
        self.last_prices: Dict[str, float] = {}
        self.fill_history: Dict[str, collections.deque] = {} # Logic for future feedback

    def update(self, product: str, mid: float):
        if product not in self.mid_history:
            self.mid_history[product] = collections.deque(maxlen=10)
            self.vola_ema[product] = 0.0
            self.last_prices[product] = mid
            
        self.mid_history[product].append(mid)
        
        # Volatility update
        diff = abs(mid - self.last_prices[product])
        self.vola_ema[product] = (Config.VOLA_EMA_ALPHA * diff) + (1.0 - Config.VOLA_EMA_ALPHA) * self.vola_ema[product]
        self.last_prices[product] = mid

    def get_momentum(self, product: str) -> float:
        history = self.mid_history.get(product)
        if not history or len(history) < 2: return 0.0
        return history[-1] - history[-2]

    def get_trend_confirmation(self, product: str, direction: str) -> bool:
        """Confirms if last 3 ticks confirm the direction."""
        history = self.mid_history.get(product)
        if not history or len(history) < 3: return False
        
        if direction == "UP":
            return history[-1] > history[-2] >= history[-3]
        if direction == "DOWN":
            return history[-1] < history[-2] <= history[-3]
        return False

class LeadLagAlphaEngine:
    def __init__(self, window: int):
        self.window = window
        self.price_history: Dict[str, collections.deque] = {}
        self.returns_history: Dict[str, collections.deque] = {}

    def update(self, product: str, price: float):
        if product not in self.price_history:
            self.price_history[product] = collections.deque(maxlen=self.window)
            self.returns_history[product] = collections.deque(maxlen=self.window)
        
        if len(self.price_history[product]) > 0:
            last_p = self.price_history[product][-1]
            ret = (price - last_p) / (last_p + 1e-9)
            self.returns_history[product].append(ret)
        self.price_history[product].append(price)

    def get_lead_lag_signal(self, target_prod: str, all_prods: List[str]) -> float:
        if target_prod not in self.returns_history or len(self.returns_history[target_prod]) < 2:
            return 0.0
        target_ret = self.returns_history[target_prod][-1]
        bias = 0.0
        for other in all_prods:
            if other == target_prod or other not in self.returns_history: continue
            if len(self.returns_history[other]) < 2: continue
            other_ret = self.returns_history[other][-1]
            divergence = other_ret - target_ret
            if abs(divergence) > Config.CORR_THRESHOLD:
                bias += divergence * Config.LEAD_LAG_WEIGHT
        return max(-1.0, min(1.0, bias))

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = Config.DEFAULT_LIMIT

    def __init__(self):
        self.state = StateManager()
        self.alpha_engine = LeadLagAlphaEngine(Config.CORR_WINDOW)

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        
        all_prods = list(state.order_depths.keys())
        
        for product, depth in state.order_depths.items():
            orders: List[Order] = []
            pos = state.position.get(product, 0)
            limit = Config.POSITION_LIMITS.get(product, Config.DEFAULT_LIMIT)
            
            # --- 1. Order Book Pulse ---
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid is None or best_ask is None: continue
            
            mid = (best_bid + best_ask) / 2.0
            self.state.update(product, mid)
            self.alpha_engine.update(product, mid)
            
            vwap, imbalance = self._get_ob_stats(depth)
            
            # --- 2. Signal Fusion & Fair Price ---
            ll_signal = self.alpha_engine.get_lead_lag_signal(product, all_prods)
            
            if product == "EMERALDS":
                fair = 10000.0
            else:
                fair = vwap + (Config.MICRO_ADJUSTMENT_K * imbalance)
                fair *= (1.0 + ll_signal * 0.05) # Subtle shift from cross-asset
            
            edge = fair - mid
            momentum = self.state.get_momentum(product)
            vola = self.state.vola_ema.get(product, 0.0)
            
            # --- 3. Dynamic Spread & Confidence Sizing ---
            spread_market = best_ask - best_bid
            liq_penalty = max(0, (spread_market - Config.BASE_SPREAD.get(product, 2)) * 0.5)
            
            vola_penalty = (vola * Config.VOLA_PENALTY_FACTOR) if vola > Config.VOLA_GUARD_THRESHOLD else 0.0
            
            dynamic_spread = Config.BASE_SPREAD.get(product, 2) + vola_penalty + liq_penalty - abs(ll_signal * 1.5)
            dynamic_spread = max(Config.MIN_SPREAD, min(Config.MAX_SPREAD, int(round(dynamic_spread))))
            
            # Confidence Sizing: scale by edge strength
            confidence = min(abs(edge) / Config.EDGE_SCALE, 1.0)
            base_size = Config.BASE_ORDER_SIZE * (Config.SIZE_REDUCTION_FACTOR if vola > Config.VOLA_GUARD_THRESHOLD else 1.0)
            size_on_edge = max(1, int(base_size * confidence))
            
            # --- 4. Quoting Engine (The Edge-Based Core) ---
            skew = -math.copysign((abs(pos) / limit) ** 1.5, pos) * Config.SKEW_FACTOR
            skew += ll_signal * 2.0
            
            if product == "EMERALDS": skew = 0
            
            # DEFENSIVE / AGGRESSIVE LOGIC
            # Bid Logic
            if edge > Config.STRONG_THRESHOLD:
                bid_price = best_bid + 1 # Aggressive join/beat
            elif edge > Config.WEAK_THRESHOLD:
                bid_price = best_bid # Match
            else:
                bid_price = best_bid - 1 # Defensive
                
            # Ask Logic
            if edge < -Config.STRONG_THRESHOLD:
                ask_price = best_ask - 1
            elif edge < -Config.WEAK_THRESHOLD:
                ask_price = best_ask
            else:
                ask_price = best_ask + 1

            # Inventory Adjustments to Quotes
            bid_price += int(math.floor(skew))
            ask_price += int(math.ceil(skew))

            # Anti-Adverse Selection (Momentum Filter)
            can_buy = limit - pos
            can_sell = -(limit + pos)
            
            buy_size = min(can_buy, size_on_edge)
            sell_size = max(can_sell, -size_on_edge)

            # Block buys if momentum is sharp down, sells if sharp up
            if buy_size > 0 and momentum < -0.5: buy_size = int(buy_size * 0.2)
            if sell_size < 0 and momentum > 0.5: sell_size = int(sell_size * 0.2)

            # --- 5. Hard Risk Control (Liquidation) ---
            if abs(pos) >= limit * Config.LIQUIDATION_THRESHOLD:
                if pos > 0: orders.append(Order(product, best_bid, -pos))
                else: orders.append(Order(product, best_ask, abs(pos)))
                result[product] = orders
                continue

            # Produce Orders
            if buy_size > 0:
                orders.append(Order(product, bid_price, buy_size))
            if sell_size < 0:
                orders.append(Order(product, ask_price, sell_size))
                
            # --- 6. Value-Driven Aggressive (Liquidity Taker) ---
            # Only if trend confirms and real value edge vs best_ask/bid
            confirmed_up = self.state.get_trend_confirmation(product, "UP")
            confirmed_down = self.state.get_trend_confirmation(product, "DOWN")
            
            edge_vs_market_buy = fair - best_ask
            if edge_vs_market_buy > Config.ALPHA_AGGR_VALUE_THRESHOLD and can_buy > 0 and confirmed_up:
                aggr_qty = min(can_buy // 2, 8)
                if aggr_qty > 0: orders.append(Order(product, best_ask, aggr_qty))
                
            edge_vs_market_sell = best_bid - fair
            if edge_vs_market_sell > Config.ALPHA_AGGR_VALUE_THRESHOLD and can_sell < 0 and confirmed_down:
                aggr_qty = min(abs(can_sell) // 2, 8)
                if aggr_qty > 0: orders.append(Order(product, best_bid, -aggr_qty))

            print(f"[{state.timestamp}] {product:10} | Fair:{fair:7.2f} | Mid:{mid:7.2f} | Edge:{edge:5.2f} | Pos:{pos:4} | Sprd:{dynamic_spread:2} | Vol:{vola:4.2f} | Conf:{confidence:4.2f}")
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
        imb = (b_vol - a_vol) / (b_vol + a_vol + Config.EPSILON)
        return vwap, imb
