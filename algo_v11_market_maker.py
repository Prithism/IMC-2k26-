import collections
import math
from typing import Dict, List, Tuple
from datamodel import Order, OrderDepth, TradingState

class Config:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Fair Value Weights
    MID_WEIGHT = 0.7
    VWAP_WEIGHT = 0.3
    
    # Pricing & Skew
    SKEW_STRENGTH = 4.0
    BASE_SPREAD = 2.0
    
    # Thresholds
    ATTACK_THRESHOLD = 1.2
    LOSS_CUT_THRESHOLD = 4.0 # Ticks away from fair
    TREND_THRESHOLD = 0.5

class Trader:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_POSITION_LIMIT = 20

    def __init__(self):
        self.mid_history = {}

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        
        for product, depth in state.order_depths.items():
            orders = []
            pos = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, self.DEFAULT_POSITION_LIMIT)

            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid is None or best_ask is None: continue

            mid = (best_bid + best_ask) / 2.0
            
            # --- 1. Momentum & Trend ---
            if product not in self.mid_history:
                self.mid_history[product] = collections.deque(maxlen=10)
            h = self.mid_history[product]
            h.append(mid)
            trend = mid - h[0] if len(h) >= 10 else 0.0

            # VWAP
            t_val = 0; t_vol = 0
            for p, v in sorted(depth.buy_orders.items(), reverse=True)[:3]:
                t_val += p * v; t_vol += v
            for p, v in sorted(depth.sell_orders.items())[:3]:
                av = abs(v); t_val += p * av; t_vol += av
            vwap = t_val / t_vol if t_vol > 0 else mid

            # --- 2. Fair Price ---
            if product == "EMERALDS":
                fair = 10000.0
            else:
                fair = (Config.MID_WEIGHT * mid) + (Config.VWAP_WEIGHT * vwap)

            # --- 3. Strict Loss Cutting ---
            # If our position is underwater relative to Fair Price
            is_loss_buy = (pos > 0 and (best_bid - fair) < -Config.LOSS_CUT_THRESHOLD)
            is_loss_sell = (pos < 0 and (fair - best_ask) < -Config.LOSS_CUT_THRESHOLD)
            
            if is_loss_buy or is_loss_sell:
                # Emergency flatten
                if pos > 0: orders.append(Order(product, best_bid, -pos))
                else: orders.append(Order(product, best_ask, abs(pos)))
                result[product] = orders
                continue

            # --- 4. Controlled Sizing & Pyramiding ---
            base_size = 5
            
            # ATTACK MODE (Moderate 3x)
            edge_buy = fair - best_ask
            edge_sell = best_bid - fair
            attack_mult = 3.0 if (edge_buy > Config.ATTACK_THRESHOLD or edge_sell > Config.ATTACK_THRESHOLD) else 1.0
            
            # PYRAMIDING (Add 50% if trend is with us)
            pyramid_mult = 1.0
            if (pos > 0 and trend > Config.TREND_THRESHOLD) or (pos < 0 and trend < -Config.TREND_THRESHOLD):
                pyramid_mult = 1.5
            
            final_size = int(base_size * attack_mult * pyramid_mult)
            
            # --- 5. Execution Logic ---
            # Continuous Skew for Stability
            skew = -( (pos / limit) ** 1.3 ) * Config.SKEW_STRENGTH
            if product == "EMERALDS": skew = 0
            
            bid_p = int(math.floor(fair + skew - Config.BASE_SPREAD / 2.0))
            ask_p = int(math.ceil(fair + skew + Config.BASE_SPREAD / 2.0))
            
            # Competitive quoting but cautious
            bid_p = min(bid_p, best_bid + 1)
            ask_p = max(ask_p, best_ask - 1)

            can_buy = limit - pos
            can_sell = -(limit + pos)

            if can_buy > 0: orders.append(Order(product, bid_p, min(can_buy, final_size)))
            if can_sell < 0: orders.append(Order(product, ask_p, max(can_sell, -final_size)))

            result[product] = orders

        return result, 0, state.traderData
