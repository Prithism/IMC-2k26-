import collections
import math
from typing import Dict, List, Tuple
from datamodel import Order, OrderDepth, TradingState

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
            
            if best_bid is None or best_ask is None:
                continue

            mid = (best_bid + best_ask) / 2.0
            
            # 1. Update mid history for trend
            if product not in self.mid_history:
                self.mid_history[product] = collections.deque(maxlen=6)
            self.mid_history[product].append(mid)

            # --- 2. Calculate Indicators ---
            # Trend calculation (t vs t-5)
            h = self.mid_history[product]
            trend = h[-1] - h[0] if len(h) >= 6 else 0.0

            # Volumes & Imbalance
            b_vol = sum(depth.buy_orders.values())
            a_vol = sum(abs(v) for v in depth.sell_orders.values())
            total_vol = b_vol + a_vol
            imbalance = (b_vol - a_vol) / total_vol if total_vol > 0 else 0.0

            # VWAP (top 3 levels)
            t_val = 0
            t_vol_vwap = 0
            for p, v in sorted(depth.buy_orders.items(), reverse=True)[:3]:
                t_val += p * v
                t_vol_vwap += v
            for p, v in sorted(depth.sell_orders.items())[:3]:
                av = abs(v)
                t_val += p * av
                t_vol_vwap += av
            vwap = t_val / t_vol_vwap if t_vol_vwap > 0 else mid

            # Fair Price
            if product == "EMERALDS":
                fair = 10000.0
            else:
                fair = 0.7 * mid + 0.3 * vwap

            # Edge Calculation
            edge_buy = fair - best_ask
            edge_sell = best_bid - fair

            # --- 3. Inventory Control ---
            if abs(pos) > limit * 0.5:
                # Force immediate reduction
                if pos > 0:
                    orders.append(Order(product, best_bid, -pos))
                else:
                    orders.append(Order(product, best_ask, abs(pos)))
                result[product] = orders
                continue

            # --- 4. Trading Logic ---
            base_size = 6
            attack_size = base_size * 5  # 5x normal
            
            can_buy = limit - pos
            can_sell = -(limit + pos)

            # Attack mode evaluation
            attack_buy = (edge_buy > 1.0 or imbalance > 0.4)
            attack_sell = (edge_sell > 1.0 or imbalance < -0.4)
            
            # Exit Logic (don't attack if negative edge or trend reverses against)
            if edge_buy < 0 or trend < 0: attack_buy = False
            if edge_sell < 0 or trend > 0: attack_sell = False

            # Execute Buy Side
            if attack_buy and can_buy > 0:
                orders.append(Order(product, best_ask, min(can_buy, attack_size)))
            elif can_buy > 0:
                # Passive Base Layer
                bid_px = min(best_bid, int(math.floor(fair - 1)))
                orders.append(Order(product, bid_px, min(can_buy, base_size)))

            # Execute Sell Side
            if attack_sell and can_sell < 0:
                orders.append(Order(product, best_bid, max(can_sell, -attack_size)))
            elif can_sell < 0:
                # Passive Base Layer
                ask_px = max(best_ask, int(math.ceil(fair + 1)))
                orders.append(Order(product, ask_px, max(can_sell, -base_size)))

            # Logging
            print(f"[{state.timestamp}] {product:10} | Fair: {fair:.2f} | EB: {edge_buy:.2f} | ES: {edge_sell:.2f} | Imb: {imbalance:.2f} | Trend: {trend:.2f} | Pos: {pos}")
            
            result[product] = orders

        return result, 0, state.traderData
