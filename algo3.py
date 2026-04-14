import json
from typing import Dict, List
from datamodel import Order, OrderDepth, TradingState

class Trader:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    # Tight spreads for maximum trade frequency
    SPREADS = {"TOMATOES": 4, "EMERALDS": 2}
    EOD_TIMESTAMP = 999900

    def __init__(self):
        self.ema_prices = {}

    def get_weighted_mid(self, depth: OrderDepth, product: str):
        # Calculate Micro-price: weighted by volume balance
        buy_orders = depth.buy_orders
        sell_orders = depth.sell_orders
        if not buy_orders or not sell_orders:
            return None
        
        best_bid, bid_vol = max(buy_orders.items())
        best_ask, ask_vol = min(sell_orders.items())
        ask_vol = abs(ask_vol)
        
        # Micro-price = (Bid * AskVol + Ask * BidVol) / (BidVol + AskVol)
        return (best_bid * ask_vol + best_ask * bid_vol) / (bid_vol + ask_vol)

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        for product in state.order_depths:
            depth = state.order_depths[product]
            pos = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, 20)
            
            # --- PREDICTIVE PRICE ---
            mid_price = self.get_weighted_mid(depth, product)
            if mid_price is None:
                continue

            # Special case for Emeralds (Hard anchor at 10000)
            fair_price = 10000 if product == "EMERALDS" else mid_price
            
            # --- INVENTORY SKEWING (The secret to 100k) ---
            # If we are long, we want to sell more/lower. If short, buy more/higher.
            # Shift = - (current_position / limit) * aggressive_factor
            skew = - (pos / limit) * 2
            
            buy_price = int(round(fair_price - self.SPREADS.get(product, 2) + skew))
            sell_price = int(round(fair_price + self.SPREADS.get(product, 2) + skew))

            orders: List[Order] = []

            # Aggressively quote both sides
            if pos < limit:
                buy_qty = limit - pos
                orders.append(Order(product, buy_price, buy_qty))
            
            if pos > -limit:
                sell_qty = pos + limit
                orders.append(Order(product, sell_price, -sell_qty))

            # --- FAST FLATTEN AT EOD ---
            if state.timestamp >= self.EOD_TIMESTAMP - 500:
                orders = []
                if pos > 0:
                    orders.append(Order(product, int(fair_price - 5), -pos))
                elif pos < 0:
                    orders.append(Order(product, int(fair_price + 5), -pos))

            result[product] = orders

        return result, 0, ""
