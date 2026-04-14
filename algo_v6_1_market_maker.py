import collections
import math
from typing import Dict, List, Tuple
from datamodel import Order, OrderDepth, TradingState

class Config:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Pricing
    MID_WEIGHT = 0.6
    VWAP_WEIGHT = 0.4
    GAMMA = 0.12 # Inventory risk parameter
    BASE_SPREAD = 2.0
    
    # Thresholds
    ENTRY_THRESHOLD = 0.25
    ATTACK_THRESHOLD = 0.9
    SUPER_ATTACK_THRESHOLD = 1.5
    
    # Sizing
    BASE_SIZE = 5
    HARD_CAP_RATIO = 0.6 # e.g. 150 for TOMATOES, 12 for EMERALDS
    
    # Safety
    VOLA_THRESHOLD = 3.0

class StateManager:
    def __init__(self):
        self.mid_history = {}
        self.last_pnl = 0.0
        self.peak_pnl = 0.0

    def update(self, prod: str, mid: float):
        if prod not in self.mid_history:
            self.mid_history[prod] = collections.deque(maxlen=10)
        self.mid_history[prod].append(mid)

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = 20

    def __init__(self):
        self.state = StateManager()

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        
        for prod, depth in state.order_depths.items():
            orders = []
            pos = state.position.get(prod, 0)
            limit = self.POSITION_LIMITS.get(prod, self.DEFAULT_POSITION_LIMIT)
            
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if not best_bid or not best_ask: continue
            
            mid = (best_bid + best_ask) / 2.0
            self.state.update(prod, mid)
            
            # --- 1. Fair Price ---
            t_val = 0; t_vol = 0; b_vol = 0; a_vol = 0
            for p, v in sorted(depth.buy_orders.items(), reverse=True)[:3]:
                t_val += p * v; t_vol += v; b_vol += v
            for p, v in sorted(depth.sell_orders.items())[:3]:
                av = abs(v); t_val += p * av; t_vol += av; a_vol += av
            vwap = t_val / t_vol if t_vol > 0 else mid
            
            fair = (Config.MID_WEIGHT * mid + Config.VWAP_WEIGHT * vwap) if prod != "EMERALDS" else 10000.0
            
            # --- 2. Inventory-Aware Pricing ---
            gamma = Config.GAMMA if prod != "EMERALDS" else 0.02
            res_price = fair - gamma * pos
            
            # Note: Volatility control
            h = self.state.mid_history[prod]
            volatility = abs(h[-1] - h[-2]) if len(h) >= 2 else 0.0
            
            # --- 3. Dynamic Spread ---
            spread = Config.BASE_SPREAD + 1.5 * volatility + 0.8 * (abs(pos) / limit)
            
            # Scale Base Size relative to limits
            scale = limit / 20.0
            base_size = int(Config.BASE_SIZE * scale)
            size_b = base_size
            size_s = base_size
            
            # --- 11. Volatility Control ---
            if volatility > Config.VOLA_THRESHOLD:
                spread += 1.0
                size_b = max(1, int(size_b * 0.7))
                size_s = max(1, int(size_s * 0.7))
                
            # --- 4. Quoting ---
            bid_px = res_price - spread / 2.0
            ask_px = res_price + spread / 2.0
            
            # --- 5. Edge Calculation ---
            edge_buy = fair - best_ask
            edge_sell = best_bid - fair
            
            # --- 8. Position Sizing ---
            if edge_buy > Config.SUPER_ATTACK_THRESHOLD:   size_b *= 3
            elif edge_buy > Config.ATTACK_THRESHOLD:       size_b *= 2
                
            if edge_sell > Config.SUPER_ATTACK_THRESHOLD:  size_s *= 3
            elif edge_sell > Config.ATTACK_THRESHOLD:      size_s *= 2
            
            # --- 10. Imbalance Bias ---
            imb = (b_vol - a_vol) / (b_vol + a_vol + 1e-9)
            if imb > 0.5: size_b = int(size_b * 1.2)
            elif imb < -0.5: size_s = int(size_s * 1.2)
            
            can_b = limit - pos
            can_s = -(limit + pos)
            
            q_b = min(can_b, size_b)
            q_s = max(can_s, -size_s)

            # --- 6, 7, 13. Execution & Market Making ---
            # BUY SIDE
            if can_b > 0 and q_b > 0:
                if edge_buy > Config.ATTACK_THRESHOLD:
                    orders.append(Order(prod, best_ask, q_b))
                elif edge_buy > Config.ENTRY_THRESHOLD:
                    bp = min(best_bid, int(math.floor(bid_px)))
                    orders.append(Order(prod, bp, q_b))
                else: # 13. Always on
                    orders.append(Order(prod, best_bid - 1, min(can_b, 2)))

            # SELL SIDE
            if can_s < 0 and q_s < 0:
                if edge_sell > Config.ATTACK_THRESHOLD:
                    orders.append(Order(prod, best_bid, q_s))
                elif edge_sell > Config.ENTRY_THRESHOLD:
                    ap = max(best_ask, int(math.ceil(ask_px)))
                    orders.append(Order(prod, ap, q_s))
                else: # 13. Always on
                    orders.append(Order(prod, best_ask + 1, max(can_s, -2)))

            # --- 9. Inventory Reduction (Hard Cap) ---
            hard_cap = limit * Config.HARD_CAP_RATIO
            if abs(pos) > hard_cap:
                reduction = int(limit * 0.2)
                if pos > 0: orders.append(Order(prod, best_bid, -reduction))
                else: orders.append(Order(prod, best_ask, reduction))

            result[prod] = orders

        return result, 0, state.traderData
