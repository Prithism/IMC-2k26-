import json
import math
from typing import Dict, List
from datamodel import Order, OrderDepth, TradingState

class Trader:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    BASE_SPREADS = {"TOMATOES": 4, "EMERALDS": 2}
    EOD_TIMESTAMP = 999900
    # Ultra-low sensitivity: stays aggressive unless market goes crazy
    VOL_SENSITIVITY = 0.1 

    def __init__(self):
        self.ema_prices = {}
        self.vol_scores = {}

    def get_weighted_mid(self, depth: OrderDepth):
        if not depth.buy_orders or not depth.sell_orders: return None
        bb, bv = max(depth.buy_orders.items())
        ba, av = min(depth.sell_orders.items())
        return (bb * abs(av) + ba * bv) / (bv + abs(av))

    def run(self, state: TradingState):
        if state.traderData:
            try:
                data = json.loads(state.traderData)
                self.vol_scores = data.get("vol_scores", {})
                self.ema_prices = data.get("ema_prices", {})
            except json.JSONDecodeError: pass

        result: Dict[str, List[Order]] = {}

        for product in state.order_depths:
            depth = state.order_depths[product]
            pos = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, 20)
            mid = self.get_weighted_mid(depth)
            
            if mid is None: continue

            # --- ULTRA-STABLE TRACKING ---
            prev_ema = self.ema_prices.get(product, mid)
            price_change = abs(mid - prev_ema)
            
            current_vol = self.vol_scores.get(product, 0)
            new_vol = (0.95 * current_vol) + (0.05 * price_change) 
            self.vol_scores[product] = new_vol
            self.ema_prices[product] = mid

            # Max Buffer of 4 ensures we stay close to the best prices
            vol_buffer = min(int(new_vol * self.VOL_SENSITIVITY), 4) 
            current_spread = self.BASE_SPREADS.get(product, 2) + vol_buffer
            
            fair_price = 10000 if product == "EMERALDS" else mid
            skew = - (pos / limit) * 2.5 
            
            buy_price = int(round(fair_price - current_spread + skew))
            sell_price = int(round(fair_price + current_spread + skew))

            orders: List[Order] = []
            if pos < limit: orders.append(Order(product, buy_price, limit - pos))
            if pos > -limit: orders.append(Order(product, sell_price, -(pos + limit)))

            # End of Day Flush
            if state.timestamp >= self.EOD_TIMESTAMP - 500:
                orders = []
                if pos > 0: orders.append(Order(product, int(mid - 5), -pos))
                elif pos < 0: orders.append(Order(product, int(fair_price + 5), -pos))

            result[product] = orders

        trader_data = json.dumps({
            "vol_scores": self.vol_scores,
            "ema_prices": self.ema_prices
        })
        return result, 0, trader_data
