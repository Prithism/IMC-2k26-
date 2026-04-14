import collections
import math
from typing import Dict, List, Tuple
from datamodel import Order, OrderDepth, TradingState

class Config:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Fair Value Weights
    MID_WEIGHT = 0.6
    VWAP_WEIGHT = 0.3
    IMB_WEIGHT = 0.1  # Micro-price influence
    
    # Quoting
    SKEW_STRENGTH = 6.0
    BASE_SPREAD = 2.0
    
    # Aggressiveness
    ATTACK_THRESHOLD = 0.6 # Low threshold for high frequency
    MAX_ATTACK_MULT = 4.0

class Trader:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_POSITION_LIMIT = 20

    def __init__(self):
        self.mid_history = {}
        self.vola_ema = {}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        
        for product, depth in state.order_depths.items():
            orders = []
            pos = state.position.get(product, 0)
            limit = Config.POSITION_LIMITS.get(product, Config.DEFAULT_LIMIT)

            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid is None or best_ask is None: continue

            mid = (best_bid + best_ask) / 2.0
            
            # --- 1. Signal Processing ---
            if product not in self.mid_history:
                self.mid_history[product] = collections.deque(maxlen=10)
                self.vola_ema[product] = 0.0
            
            h = self.mid_history[product]
            if len(h) > 0:
                diff = abs(mid - h[-1])
                self.vola_ema[product] = 0.1 * diff + 0.9 * self.vola_ema[product]
            h.append(mid)
            
            # Trend (short-term momentum)
            trend = mid - h[0] if len(h) >= 10 else 0.0

            # Liquidity Imbalance
            b_vol = sum(depth.buy_orders.values())
            a_vol = sum(abs(v) for v in depth.sell_orders.values())
            imb = (b_vol - a_vol) / (b_vol + a_vol + 1e-9)

            # VWAP
            t_val = 0; t_vol = 0
            for p, v in sorted(depth.buy_orders.items(), reverse=True)[:3]:
                t_val += p * v; t_vol += v
            for p, v in sorted(depth.sell_orders.items())[:3]:
                av = abs(v); t_val += p * av; t_vol += av
            vwap = t_val / t_vol if t_vol > 0 else mid

            # --- 2. Fair Price (Micro-Price Integrated) ---
            if product == "EMERALDS":
                fair = 10000.0
            else:
                fair = (Config.MID_WEIGHT * mid) + (Config.VWAP_WEIGHT * vwap) + (Config.IMB_WEIGHT * imb)
                # Apply short-term trend influence
                fair += trend * 0.2

            # --- 3. Inventory Skew (Continuous polynomial) ---
            # This pushes prices down if we are long, and up if we are short
            inventory_pct = pos / limit
            skew = -(inventory_pct ** 3) * Config.SKEW_STRENGTH

            # --- 4. Adaptive Spreading ---
            # Widen spread if volatility is high
            vola_adj = self.vola_ema[product] * 0.5
            current_spread = max(Config.BASE_SPREAD, 1.0 + vola_adj)

            # --- 5. Order Placement ---
            bid_price = int(math.floor(fair + skew - current_spread / 2.0))
            ask_price = int(math.ceil(fair + skew + current_spread / 2.0))
            
            # Constraints: Don't cross the mid unless we have extreme edge
            bid_price = min(bid_price, best_bid + 1)
            ask_price = max(ask_price, best_ask - 1)

            # Dynamic Sizing
            edge_buy = fair - best_ask
            edge_sell = best_bid - fair
            
            base_size = 8
            # Scale size by edge & confidence
            buy_mult = 1.0 + max(0.0, edge_buy / Config.ATTACK_THRESHOLD)
            sell_mult = 1.0 + max(0.0, edge_sell / Config.ATTACK_THRESHOLD)
            
            buy_size = min(limit - pos, int(base_size * min(Config.MAX_ATTACK_MULT, buy_mult)))
            sell_size = min(limit + pos, int(base_size * min(Config.MAX_ATTACK_MULT, sell_mult)))

            if buy_size > 0: orders.append(Order(product, bid_price, buy_size))
            if sell_size > 0: orders.append(Order(product, ask_price, -sell_size))

            # Hard Liquidation Check (Emergency)
            if abs(inventory_pct) > 0.9:
                orders = [] # Clear and flatten
                if pos > 0: orders.append(Order(product, best_bid, -pos))
                else: orders.append(Order(product, best_ask, abs(pos)))

            result[product] = orders
            # print(f"[{state.timestamp}] {product:10} | Fair: {fair:.2f} | Skew: {skew:.2f} | Pos: {pos}")

        return result, 0, state.traderData
