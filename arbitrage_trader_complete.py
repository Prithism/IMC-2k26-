from datamodel import Order, OrderDepth, TradingState, Listing
import json
from typing import Dict, List

# This is basically algo3.py logic but inside a file the backtester can see
class Trader:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    SPREADS = {"TOMATOES": 4, "EMERALDS": 2} # Very tight
    EOD_TIMESTAMP = 999900

    def __init__(self):
        self.ema_prices = {}

    def get_weighted_mid(self, depth: OrderDepth, product: str):
        buy_orders = depth.buy_orders
        sell_orders = depth.sell_orders
        if not buy_orders or not sell_orders:
            return None
        
        best_bid, bid_vol = max(buy_orders.items())
        best_ask, ask_vol = min(sell_orders.items())
        ask_vol = abs(ask_vol)
        
        return (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        for product in state.order_depths:
            depth = state.order_depths[product]
            pos = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, 20)
            
            mid_price = self.get_weighted_mid(depth, product)
            if mid_price is None:
                continue

            fair_price = 10000 if product == "EMERALDS" else mid_price
            
            # Inventory Skewing: move prices to encourage getting back to zero
            # If pos is +250, skew is -2.5. We sell lower and buy lower.
            skew = - (pos / limit) * 2.5
            
            buy_price = int(round(fair_price - self.SPREADS.get(product, 2) + skew))
            sell_price = int(round(fair_price + self.SPREADS.get(product, 2) + skew))

            orders: List[Order] = []

            # Quote BOTH sides
            if pos < limit:
                orders.append(Order(product, buy_price, limit - pos))
            
            if pos > -limit:
                orders.append(Order(product, sell_price, -(pos + limit)))

            # End of day flush
            if state.timestamp >= self.EOD_TIMESTAMP - 500:
                orders = []
                if pos > 0:
                    orders.append(Order(product, int(fair_price - 5), -pos))
                elif pos < 0:
                    orders.append(Order(product, int(fair_price + 5), -pos))

            result[product] = orders

        return result, 0, ""
