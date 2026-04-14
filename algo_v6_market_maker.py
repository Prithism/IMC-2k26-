import json
import math
import collections
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for the Market Maker V6 Strategy."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Fair Value & Microstructure
    MICRO_ADJUSTMENT_K = 0.8
    LEVELS_TO_CONSIDER = 5
    EPSILON = 1e-6
    
    # Spreads & Quoting
    BASE_SPREAD = {"TOMATOES": 4, "EMERALDS": 2}
    MIN_SPREAD = 2
    MAX_SPREAD = 40
    
    # Inventory Management
    LIQUIDATION_THRESHOLD = 0.8
    SKEW_FACTOR = 4.0
    
    # Alpha & Aggressive
    ALPHA_AGGR_THRESHOLD = 1.0 # Lowered for more frequent fills
    
    # Cross-Asset Alpha (Lead-Lag)
    CORR_WINDOW = 10
    CORR_THRESHOLD = 0.00005 # More sensitive
    LEAD_LAG_WEIGHT = 0.5 
    
    # Volatility & Liquidity
    VOLA_EMA_ALPHA = 0.1
    LIQUIDITY_PENALTY_FACTOR = 0.5

class LeadLagAlphaEngine:
    """Tracks cross-asset movements to detect lead-lag opportunities."""
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

    def get_alpha_signal(self, target_prod: str, all_prods: List[str]) -> float:
        """Returns a directional bias (-1 to 1) based on lead-lag alpha."""
        if target_prod not in self.returns_history or len(self.returns_history[target_prod]) < 2:
            return 0.0
            
        target_ret = self.returns_history[target_prod][-1]
        alpha = 0.0
        
        for other in all_prods:
            if other == target_prod or other not in self.returns_history:
                continue
            
            if len(self.returns_history[other]) < 2:
                continue
                
            other_ret = self.returns_history[other][-1]
            
            # Simple Lead-Lag heuristic:
            # If 'other' moved significantly more than 'target' in the last tick
            # and they are generally correlated (using a simple sign-match check or fixed known pairs)
            # Emeralds and Tomatoes aren't necessarily correlated, but we'll implement the logic generally.
            
            divergence = other_ret - target_ret
            if abs(divergence) > Config.CORR_THRESHOLD:
                # We assume a positive correlation for this heuristic
                alpha += divergence * Config.LEAD_LAG_WEIGHT
                
        return max(-1.0, min(1.0, alpha))

class StateManager:
    def __init__(self):
        self.vola_ema: Dict[str, float] = {}
        self.last_prices: Dict[str, float] = {}

    def update_volatility(self, product: str, price: float) -> float:
        if product not in self.last_prices:
            self.last_prices[product] = price
            self.vola_ema[product] = 0.0
            return 0.0
        diff = abs(price - self.last_prices[product])
        current_vola = self.vola_ema.get(product, diff)
        new_vola = (Config.VOLA_EMA_ALPHA * diff) + (1.0 - Config.VOLA_EMA_ALPHA) * current_vola
        self.vola_ema[product] = new_vola
        self.last_prices[product] = price
        return new_vola

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = Config.DEFAULT_LIMIT

    def __init__(self):
        self.state = StateManager()
        self.alpha_engine = LeadLagAlphaEngine(Config.CORR_WINDOW)

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        
        # Pre-update alpha engine with current mid prices
        all_prods = list(state.order_depths.keys())
        for product, depth in state.order_depths.items():
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid and best_ask:
                mid = (best_bid + best_ask) / 2.0
                self.alpha_engine.update(product, mid)

        for product, depth in state.order_depths.items():
            orders: List[Order] = []
            pos = state.position.get(product, 0)
            limit = Config.POSITION_LIMITS.get(product, Config.DEFAULT_LIMIT)
            
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid is None or best_ask is None: continue
            
            mid = (best_bid + best_ask) / 2.0
            vwap, imbalance = self.get_orderbook_stats(depth)
            
            # --- 1. Cross-Asset Alpha Module ---
            corr_alpha_signal = self.alpha_engine.get_alpha_signal(product, all_prods)
            
            # 2. Fair Price Computation
            if product == "EMERALDS":
                fair = 10000.0
            else:
                # Base fair from orderbook + lead-lag alpha adjustment
                fair = vwap + (Config.MICRO_ADJUSTMENT_K * imbalance)
                # Apply Lead-Lag shift (e.g. if leader went up 0.1%, we shift fair by small amount)
                fair *= (1.0 + corr_alpha_signal * 0.1) # 10bps max shift from alpha
                
            # 3. Dynamic Spread & Volatility
            vola = self.state.update_volatility(product, mid)
            spread_market = best_ask - best_bid
            low_liq_penalty = max(0, (spread_market - Config.BASE_SPREAD.get(product, 2)) * Config.LIQUIDITY_PENALTY_FACTOR)
            
            base_s = Config.BASE_SPREAD.get(product, 2)
            dynamic_spread = base_s + (vola * 2.0) - abs(corr_alpha_signal * 2.0) + low_liq_penalty
            dynamic_spread = max(Config.MIN_SPREAD, min(Config.MAX_SPREAD, int(round(dynamic_spread))))
            
            # 4. Inventory Skewing
            # Bias skew further if correlative alpha exists
            skew = -math.copysign((abs(pos) / limit) ** 1.5, pos) * Config.SKEW_FACTOR
            skew += corr_alpha_signal * 2.0 # Push quotes in direction of leader
            
            if product == "EMERALDS": skew = 0
            
            # 5. Risk Control (Liquidation)
            if abs(pos) >= limit * Config.LIQUIDATION_THRESHOLD:
                if pos > 0: orders.append(Order(product, best_bid, -pos))
                else: orders.append(Order(product, best_ask, abs(pos)))
                result[product] = orders
                continue

            # 6. Execution: Competitive Quoting
            bid_price = int(math.floor(fair + skew - dynamic_spread / 2.0))
            ask_price = int(math.ceil(fair + skew + dynamic_spread / 2.0))
            
            # BE COMPETITIVE: Try to match the best bid/ask to ensure fills
            # In a competitive market, being 1 tick off means 0 fills.
            bid_price = max(bid_price, best_bid)
            ask_price = min(ask_price, best_ask)
            
            # Final sanity check: don't cross the spread unless alpha is high
            if abs(corr_alpha_signal) < 0.5:
                bid_price = min(bid_price, best_ask - 1)
                ask_price = max(ask_price, best_bid + 1)
            
            can_buy = limit - pos
            can_sell = -(limit + pos)
            
            # MM Quotes: Slightly larger slices
            if can_buy > 0: orders.append(Order(product, bid_price, min(can_buy, 25)))
            if can_sell < 0: orders.append(Order(product, ask_price, max(can_sell, -25)))
                
            # 7. Value-Based Alpha (Aggressive)
            # Trigger aggressive trade if lead-lag signal is strong
            if corr_alpha_signal > 0.5 and can_buy > 0:
                orders.append(Order(product, best_ask, min(can_buy // 2, 5)))
            elif corr_alpha_signal < -0.5 and can_sell < 0:
                orders.append(Order(product, best_bid, max(can_sell // 2, -5)))

            print(f"[{state.timestamp}] {product:10} | Fair: {fair:8.2f} | Alpha: {corr_alpha_signal:5.2f} | Pos: {pos:4} | Spread: {dynamic_spread:2}")
            result[product] = orders
            
        return result, conversions, state.traderData

    def get_orderbook_stats(self, depth: OrderDepth) -> Tuple[float, float]:
        b = sorted(depth.buy_orders.items(), reverse=True)[:Config.LEVELS_TO_CONSIDER]
        s = sorted(depth.sell_orders.items())[:Config.LEVELS_TO_CONSIDER]
        if not b or not s: return 0.0, 0.0
        t_vol = 0
        t_val = 0
        b_vol = 0
        a_vol = 0
        for p, v in b:
            t_val += p * v
            t_vol += v
            b_vol += v
        for p, v in s:
            av = abs(v)
            t_val += p * av
            t_vol += av
            a_vol += av
        vwap = t_val / t_vol if t_vol > 0 else 0.0
        imb = (b_vol - a_vol) / (b_vol + a_vol + Config.EPSILON)
        return vwap, imb
