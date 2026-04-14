import json
from typing import Dict, List
from datamodel import Order, OrderDepth, TradingState

class Trader:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    EOD_TIMESTAMP = 999900
    EMA_ALPHA = 0.2
    STOP_LOSS_LIMIT = -450
    
    def __init__(self):
        self.fair_values = {}
        self.ema_prices = {}
        self.cumulative_pnl = 0

    def calculate_vwap(self, order_depth: OrderDepth):
        total_value, total_vol = 0, 0
        for price, vol in list(order_depth.buy_orders.items()) + list(order_depth.sell_orders.items()):
            total_value += price * abs(vol)
            total_vol += abs(vol)
        return total_value / total_vol if total_vol > 0 else 0

    def run(self, state: TradingState):
        if state.traderData:
            try:
                data = json.loads(state.traderData)
                self.ema_prices = data.get("ema_prices", {})
                self.cumulative_pnl = data.get("pnl", 0)
            except json.JSONDecodeError:
                pass

        if self.cumulative_pnl < self.STOP_LOSS_LIMIT:
            return {}, 0, state.traderData

        result: Dict[str, List[Order]] = {}

        for product in state.order_depths:
            depth = state.order_depths[product]
            pos = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, 20)
            
            current_vwap = self.calculate_vwap(depth)
            if product not in self.ema_prices:
                self.ema_prices[product] = current_vwap
            
            self.ema_prices[product] = (self.EMA_ALPHA * current_vwap) + ((1 - self.EMA_ALPHA) * self.ema_prices[product])
            fair_value = self.ema_prices[product]

            orders: List[Order] = []
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else fair_value - 2
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else fair_value + 2

            if best_ask < fair_value - 1 and pos < limit:
                buy_qty = min(-depth.sell_orders[best_ask], limit - pos)
                orders.append(Order(product, best_ask, buy_qty))

            if best_bid > fair_value + 1 and pos > -limit:
                sell_qty = min(depth.buy_orders[best_bid], pos + limit)
                orders.append(Order(product, best_bid, -sell_qty))

            if state.timestamp >= self.EOD_TIMESTAMP - 1000:
                orders = []
                if pos > 0 and depth.buy_orders:
                    orders.append(Order(product, max(depth.buy_orders.keys()), -pos))
                elif pos < 0 and depth.sell_orders:
                    orders.append(Order(product, min(depth.sell_orders.keys()), -pos))

            result[product] = orders

        trader_data = json.dumps({
            "ema_prices": self.ema_prices,
            "pnl": self.cumulative_pnl
        })

        return result, 0, trader_data
