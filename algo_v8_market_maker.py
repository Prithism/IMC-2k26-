import json
import math
import collections
from typing import Dict, List, Optional, Tuple, Any
from datamodel import Order, OrderDepth, TradingState, Listing

class Config:
    """Tunable parameters for Elite Selective Market Maker V9."""
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Strict Edge Thresholds (Calculated as Maker Edge)
    # Edge = Fair - MyPrice
    EDGE_THRESHOLD = 1.0   
    
    # Fair Value Mix
    MID_WEIGHT = 0.7
    VWAP_WEIGHT = 0.3
    
    # Trend Filter
    TREND_WINDOW = 10
    STRONG_TREND_THRESHOLD = 0.8 # Ticks move
    
    # Inventory Control (STRICT)
    LIQUIDATION_THRESHOLD = 0.4 
    
    # Sizing
    BASE_ORDER_SIZE = 25
    EDGE_SCALE = 2.0  

class StateManager:
    def __init__(self):
        self.mid_history: Dict[str, collections.deque] = {}

    def update(self, product: str, mid: float):
        if product not in self.mid_history:
            self.mid_history[product] = collections.deque(maxlen=20)
        self.mid_history[product].append(mid)

    def get_trend(self, product: str) -> float:
        history = self.mid_history.get(product)
        if not history or len(history) < 11: return 0.0
        return history[-1] - history[-11]

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = Config.DEFAULT_LIMIT

    def __init__(self):
        self.state = StateManager()

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        conversions = 0
        
        for product, depth in state.order_depths.items():
            orders: List[Order] = []
            pos = state.position.get(product, 0)
            limit = Config.POSITION_LIMITS.get(product, Config.DEFAULT_LIMIT)
            
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if best_bid is None or best_ask is None: continue
            
            mid = (best_bid + best_ask) / 2.0
            self.state.update(product, mid)
            
            # 1. Fair Price Calculation
            if product == "EMERALDS":
                fair = 10000.0
            else:
                vwap = self._get_vwap(depth)
                fair = (Config.MID_WEIGHT * mid) + (Config.VWAP_WEIGHT * vwap)
            
            # 2. Trend Filtering
            trend = self.state.get_trend(product)
            is_up_trend = trend > Config.STRONG_TREND_THRESHOLD
            is_down_trend = trend < -Config.STRONG_TREND_THRESHOLD
            if product == "EMERALDS": is_up_trend = is_down_trend = False
            
            # 3. Inventory Controls (Liquidation)
            if abs(pos) >= limit * Config.LIQUIDATION_THRESHOLD:
                if pos > 0: orders.append(Order(product, best_bid, -pos))
                else: orders.append(Order(product, best_ask, abs(pos)))
                result[product] = orders
                continue

            # 4. Elite Maker Entry Logic
            # Only buy if best_bid offers positive edge vs fair
            # Edge = Fair - BestBid
            edge_buy = fair - best_bid
            if edge_buy >= Config.EDGE_THRESHOLD and not is_down_trend:
                # We can match best_bid or improve it by 1 if edge remains positive
                bid_price = best_bid
                if (fair - (best_bid + 1)) >= Config.EDGE_THRESHOLD:
                    bid_price = best_bid + 1
                
                size = int(Config.BASE_ORDER_SIZE * (1 + (edge_buy / Config.EDGE_SCALE)))
                buy_qty = min(limit - pos, size)
                if buy_qty > 0:
                    orders.append(Order(product, bid_price, buy_qty))

            # Only sell if best_ask offers positive edge vs fair
            # Edge = BestAsk - Fair
            edge_sell = best_ask - fair
            if edge_sell >= Config.EDGE_THRESHOLD and not is_up_trend:
                ask_price = best_ask
                if ((best_ask - 1) - fair) >= Config.EDGE_THRESHOLD:
                    ask_price = best_ask - 1
                
                size = int(Config.BASE_ORDER_SIZE * (1 + (edge_sell / Config.EDGE_SCALE)))
                sell_qty = min(limit + pos, size)
                if sell_qty > 0:
                    orders.append(Order(product, ask_price, -sell_qty))

            print(f"[{state.timestamp}] {product:10} | Fair:{fair:7.2f} | B_Edge:{edge_buy:5.2f} | S_Edge:{edge_sell:5.2f} | Pos:{pos:4}")
            result[product] = orders
            
        return result, conversions, state.traderData

    def _get_vwap(self, depth: OrderDepth) -> float:
        b = sorted(depth.buy_orders.items(), reverse=True)[:3]
        s = sorted(depth.sell_orders.items())[:3]
        if not b or not s: return 0.0
        t_vol = 0; t_val = 0
        for p, v in b: t_val += p * v; t_vol += v
        for p, v in s: av = abs(v); t_val += p * av; t_vol += av
        return t_val / t_vol if t_vol > 0 else 0.0
