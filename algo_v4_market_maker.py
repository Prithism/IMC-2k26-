import json
import math
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for the Market Maker V4 Strategy."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Fair Value & Microstructure
    MICRO_ADJUSTMENT_K = 0.5
    LEVELS_TO_CONSIDER = 3
    
    # Spreads & Quoting
    BASE_SPREAD = {"TOMATOES": 4, "EMERALDS": 2} # Ticks
    MIN_SPREAD = 2
    MAX_SPREAD = 30
    
    # Inventory Management
    SKEW_FACTOR = 3.5
    ALPHA_THRESHOLD = 1.5
    
    # Volatility
    VOLA_WINDOW = 20
    VOLA_SENSITIVITY = 1.2
    
    # Alpha Execution
    AGGR_THRESHOLD = 2.0
    AGGR_SIZE_RATIO = 0.5 # Percentage of available limit for aggressive trades

class OrderBookProcessor:
    @staticmethod
    def get_v_weighted_fair(depth: OrderDepth, levels: int) -> Optional[float]:
        b = sorted(depth.buy_orders.items(), reverse=True)[:levels]
        s = sorted(depth.sell_orders.items())[:levels]
        
        if not b or not s:
            return None
            
        total_vol = 0
        total_val = 0
        
        for p, v in b:
            total_val += p * v
            total_vol += v
        for p, v in s:
            abs_v = abs(v)
            total_val += p * abs_v
            total_vol += abs_v
            
        return total_val / total_vol if total_vol > 0 else None

    @staticmethod
    def get_liquidity_imbalance(depth: OrderDepth) -> float:
        best_bid_vol = max(depth.buy_orders.values()) if depth.buy_orders else 0
        best_ask_vol = abs(min(depth.sell_orders.values())) if depth.sell_orders else 0
        total = best_bid_vol + best_ask_vol
        if total == 0: return 0
        return (best_bid_vol - best_ask_vol) / total

class FairPriceEngine:
    def __init__(self, k: float, levels: int):
        self.k = k
        self.levels = levels

    def compute_fair(self, product: str, depth: OrderDepth) -> Optional[float]:
        if product == "EMERALDS":
            return 10000.0 # Standard fair value for Emeralds
            
        v_weighted = OrderBookProcessor.get_v_weighted_fair(depth, self.levels)
        if v_weighted is None: return None
        
        imbalance = OrderBookProcessor.get_liquidity_imbalance(depth)
        return v_weighted + (self.k * imbalance)

class AlphaSignalEngine:
    def __init__(self, threshold: float):
        self.threshold = threshold

    def get_signal(self, fair: float, mid: float) -> str:
        diff = fair - mid
        if diff > self.threshold: return "BULLISH"
        if diff < -self.threshold: return "BEARISH"
        return "NEUTRAL"

class InventoryManager:
    def __init__(self, skew_factor: float):
        self.skew_factor = skew_factor

    def get_skew(self, pos: int, limit: int, signal: str) -> float:
        # Nonlinear skew calculation
        base_skew = -math.copysign((pos / limit) ** 2, pos) * self.skew_factor
        
        # Signal-based adjustment
        if signal == "BULLISH": base_skew += 0.5
        elif signal == "BEARISH": base_skew -= 0.5
        
        return base_skew

class SpreadManager:
    def __init__(self, base_spread: int, vol_sensitivity: float):
        self.base_spread = base_spread
        self.vol_sensitivity = vol_sensitivity

    def get_dynamic_spread(self, volatility: float, signal_strength: float) -> int:
        spread = self.base_spread + (volatility * self.vol_sensitivity)
        # Tighten spread if signal is strong, widen if volatile
        if signal_strength > 2.0: spread -= 1
        return max(Config.MIN_SPREAD, min(Config.MAX_SPREAD, int(round(spread))))

class VolatilityModel:
    def __init__(self, window_size: int):
        self.window_size = window_size
        self.prices: Dict[str, List[float]] = {}

    def update(self, product: str, price: float) -> float:
        if product not in self.prices:
            self.prices[product] = []
        self.prices[product].append(price)
        if len(self.prices[product]) > self.window_size:
            self.prices[product].pop(0)
            
        if len(self.prices[product]) < 2:
            return 0.0
            
        # Compute range-based volatility as a proxy
        return (max(self.prices[product]) - min(self.prices[product])) / 2.0

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = Config.DEFAULT_LIMIT

    def __init__(self):
        self.fair_engine = FairPriceEngine(Config.MICRO_ADJUSTMENT_K, Config.LEVELS_TO_CONSIDER)
        self.alpha_engine = AlphaSignalEngine(Config.ALPHA_THRESHOLD)
        self.inv_manager = InventoryManager(Config.SKEW_FACTOR)
        self.vol_model = VolatilityModel(Config.VOLA_WINDOW)
        # We store some state across iterations if needed
        self.ema_vol: Dict[str, float] = {}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        
        for product, depth in state.order_depths.items():
            orders: List[Order] = []
            pos = state.position.get(product, 0)
            limit = Config.POSITION_LIMITS.get(product, Config.DEFAULT_LIMIT)
            
            # 1. Process Orderbook & Fair Price
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid is None or best_ask is None: continue
            
            mid = (best_bid + best_ask) / 2.0
            fair = self.fair_engine.compute_fair(product, depth)
            if fair is None: fair = mid
            
            # 2. Volatility Tracking
            raw_vol = self.vol_model.update(product, mid)
            self.ema_vol[product] = (0.2 * raw_vol) + (0.8 * self.ema_vol.get(product, raw_vol))
            
            # 3. Alpha Signaling
            signal = self.alpha_engine.get_signal(fair, mid)
            signal_strength = abs(fair - mid)
            
            # 4. Inventory Skewing
            skew = self.inv_manager.get_skew(pos, limit, signal)
            
            # Extra protection for Emeralds: ignore skew if it doesn't make sense
            if product == "EMERALDS":
                skew = 0 # Emeralds are mean reverting to 10k, skew is dangerous
            
            # 5. Dynamic Spread
            base_s = Config.BASE_SPREAD.get(product, 2)
            spread_manager = SpreadManager(base_s, Config.VOLA_SENSITIVITY)
            dynamic_spread = spread_manager.get_dynamic_spread(self.ema_vol[product], signal_strength)
            
            # 6. Execution: Quoting Logic
            bid_price = int(math.floor(fair - dynamic_spread / 2.0 + skew))
            ask_price = int(math.ceil(fair + dynamic_spread / 2.0 + skew))
            
            # Avoid crossing the current market unless intentional
            bid_price = min(bid_price, best_bid + 1 if best_bid else bid_price) 
            ask_price = max(ask_price, best_ask - 1 if best_ask else ask_price)
            
            # Market Making Orders: Use smaller steps to avoid getting filled on whole limit at once
            # This is safer in high-volatility or "always fill" scenarios
            order_size = max(1, limit // 4) 
            
            buy_qty = min(order_size, limit - pos)
            sell_qty = max(-order_size, -limit - pos)
            
            if buy_qty > 0:
                orders.append(Order(product, bid_price, buy_qty))
            if sell_qty < 0:
                orders.append(Order(product, ask_price, sell_qty))
                
            # 7. Alpha Aggressive Trades
            if signal == "BULLISH" and signal_strength >= Config.AGGR_THRESHOLD:
                # Place aggressive buy slightly deeper into book or at best ask if cheap
                aggr_price = best_ask
                aggr_qty = int(buy_qty * Config.AGGR_SIZE_RATIO)
                if aggr_qty > 0:
                    orders.append(Order(product, aggr_price, aggr_qty))
                    
            elif signal == "BEARISH" and signal_strength >= Config.AGGR_THRESHOLD:
                aggr_price = best_bid
                aggr_qty = int(abs(sell_qty) * Config.AGGR_SIZE_RATIO)
                if aggr_qty > 0:
                    orders.append(Order(product, aggr_price, -aggr_qty))

            # 8. Logging (Formatted as debug output)
            print(f"[{state.timestamp}] {product:10} | Fair: {fair:8.2f} | Mid: {mid:8.2f} | Pos: {pos:4} | Sig: {signal:8} | Spread: {dynamic_spread:2} | Skew: {skew:5.2f}")
            
            result[product] = orders
            
        return result, conversions, state.traderData
