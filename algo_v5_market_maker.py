import json
import math
import collections
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for the Market Maker V5 Strategy."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Fair Value & Microstructure
    MICRO_ADJUSTMENT_K = 0.8  # Stronger imbalance weight
    LEVELS_TO_CONSIDER = 5
    EPSILON = 1e-6
    
    # Spreads & Quoting
    BASE_SPREAD = {"TOMATOES": 4, "EMERALDS": 2}
    MIN_SPREAD = 2
    MAX_SPREAD = 40
    
    # Inventory Management
    LIQUIDATION_THRESHOLD = 0.8  # 80% of limit
    SKEW_FACTOR = 4.0
    
    # Alpha & Aggressive
    ALPHA_AGGR_THRESHOLD = 2.5 # Minimum edge to cross spread
    
    # Volatility & Liquidity
    VOLA_EMA_ALPHA = 0.1
    LIQUIDITY_PENALTY_FACTOR = 0.5
    
    # Feedback Loop
    TARGET_FILL_RATE = 0.2
    ADAPTIVE_STEP = 0.1

class StateManager:
    """Persists execution stats and market state across ticks."""
    def __init__(self):
        self.vola_ema: Dict[str, float] = {}
        self.last_prices: Dict[str, float] = {}
        self.fill_stats: Dict[str, Dict[str, float]] = {} # Track fills/orders
        self.prev_pos: Dict[str, int] = {}

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

class OrderBookProcessor:
    @staticmethod
    def get_multi_level_stats(depth: OrderDepth, levels: int) -> Tuple[float, float]:
        """Returns (v_weighted_price, imbalance)"""
        b = sorted(depth.buy_orders.items(), reverse=True)[:levels]
        s = sorted(depth.sell_orders.items())[:levels]
        
        if not b or not s: return 0.0, 0.0
        
        total_vol = 0
        total_val = 0
        bid_vol = 0
        ask_vol = 0
        
        for p, v in b:
            total_val += p * v
            total_vol += v
            bid_vol += v
        for p, v in s:
            av = abs(v)
            total_val += p * av
            total_vol += av
            ask_vol += av
            
        vwap = total_val / total_vol if total_vol > 0 else 0.0
        imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol + Config.EPSILON)
        return vwap, imbalance

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = Config.DEFAULT_LIMIT

    def __init__(self):
        self.state = StateManager()

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        
        # Load persistent state if available (simplified for this script version)
        # In production, we would parse state.traderData
        
        for product, depth in state.order_depths.items():
            orders: List[Order] = []
            pos = state.position.get(product, 0)
            limit = Config.POSITION_LIMITS.get(product, Config.DEFAULT_LIMIT)
            
            # 1. Market Data Analysis
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid is None or best_ask is None: continue
            
            mid = (best_bid + best_ask) / 2.0
            vwap, imbalance = OrderBookProcessor.get_multi_level_stats(depth, Config.LEVELS_TO_CONSIDER)
            
            # 2. Fair Price Computation
            if product == "EMERALDS":
                fair = 10000.0
            else:
                fair = vwap + (Config.MICRO_ADJUSTMENT_K * imbalance)
                
            # 3. Volatility & Adaptive Spread
            vola = self.state.update_volatility(product, mid)
            
            # Liquidity Penalty (wide book = higher penalty)
            spread_market = best_ask - best_bid
            low_liq_penalty = max(0, (spread_market - Config.BASE_SPREAD.get(product, 2)) * Config.LIQUIDITY_PENALTY_FACTOR)
            
            signal_strength = abs(fair - mid)
            
            base_s = Config.BASE_SPREAD.get(product, 2)
            dynamic_spread = base_s + (vola * 2.0) - min(signal_strength, 1.5) + low_liq_penalty
            dynamic_spread = max(Config.MIN_SPREAD, min(Config.MAX_SPREAD, int(round(dynamic_spread))))
            
            # 4. Inventory Risk Control & Skew
            skew = -math.copysign((abs(pos) / limit) ** 1.5, pos) * Config.SKEW_FACTOR
            if product == "EMERALDS": skew = 0 # No skew for fair-fixed assets
            
            # Hard Limit Liquidation Check
            is_liquidating = False
            if abs(pos) >= limit * Config.LIQUIDATION_THRESHOLD:
                is_liquidating = True
                print(f"[{product}] CRITICAL: Inventory breach ({pos}/{limit}). Forcing liquidation.")
                if pos > 0:
                    orders.append(Order(product, best_bid, -pos)) # Marketable sell
                else:
                    orders.append(Order(product, best_ask, abs(pos))) # Marketable buy
                result[product] = orders
                continue

            # 5. Execution Engine: Passive Quoting
            # Realistic Quoting: bid <= best_bid, ask >= best_ask
            bid_price = int(math.floor(fair + skew - dynamic_spread / 2.0))
            ask_price = int(math.ceil(fair + skew + dynamic_spread / 2.0))
            
            # Ensure we are not crossing the market unless intentional
            bid_price = min(bid_price, best_bid)
            ask_price = max(ask_price, best_ask)
            
            # Adaptive Adjustment (Feedback - Placeholder logic)
            # If we missed fills, we could tighten here.
            
            # Sizing Logic
            can_buy = limit - pos
            can_sell = -(limit + pos)
            
            # MM Quotes
            mm_buy_size = min(can_buy, 15) # Cap single order size for robustness
            mm_sell_size = max(can_sell, -15)
            
            if mm_buy_size > 0:
                orders.append(Order(product, bid_price, mm_buy_size))
            if mm_sell_size < 0:
                orders.append(Order(product, ask_price, mm_sell_size))
                
            # 6. Value-Based Alpha (Aggressive)
            # Only buy aggressively if fair price is significantly above market ask
            edge_buy = fair - best_ask
            if edge_buy > Config.ALPHA_AGGR_THRESHOLD and can_buy > 0:
                aggr_size = min(can_buy // 2, 10)
                if aggr_size > 0:
                    orders.append(Order(product, best_ask, aggr_size))
                    print(f"[{product}] ALPHA: Aggressive BUY size {aggr_size} @ {best_ask} (edge {edge_buy:.2f})")

            edge_sell = best_bid - fair
            if edge_sell > Config.ALPHA_AGGR_THRESHOLD and can_sell < 0:
                aggr_size = min(abs(can_sell) // 2, 10)
                if aggr_size > 0:
                    orders.append(Order(product, best_bid, -aggr_size))
                    print(f"[{product}] ALPHA: Aggressive SELL size {aggr_size} @ {best_bid} (edge {edge_sell:.2f})")

            # 7. Logging
            print(f"[{state.timestamp}] {product:10} | Fair: {fair:8.2f} | Mid: {mid:8.2f} | Imb: {imbalance:5.2f} | Pos: {pos:4} | Spread: {dynamic_spread:2} | Skew: {skew:5.2f}")
            
            result[product] = orders
            
        # Update previous position for fill tracking
        self.state.prev_pos = {p: state.position.get(p, 0) for p in state.order_depths}
        
        return result, conversions, state.traderData
