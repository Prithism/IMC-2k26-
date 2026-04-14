import collections
import math
from typing import Dict, List, Tuple, Any
from datamodel import Order, OrderDepth, TradingState

class Config:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_LIMIT = 20
    
    # Fair Price & Edge
    MID_WEIGHT = 0.7
    VWAP_WEIGHT = 0.3
    EDGE_TH = 0.5
    AGGR_EDGE_TH = 1.0
    NO_TRADE_ZONE = 0.3
    
    # Regime Thresholds
    VOLA_EMA_ALPHA = 0.1
    CHAOTIC_THRESHOLD = 2.2 # Lowered for higher sensitivity
    CHAOTIC_COOLDOWN = 10   # Ticks to stay defensive
    TREND_STRENGTH_THRESHOLD = 1.2
    
    # Risk
    PROFIT_LOCK_DD = 120.0 # Credits from peak
    LIQUIDATION_THRESHOLD = 0.5 # 50% limit
    
    # Sizing
    BASE_SIZE = 8
    ATTACK_SIZE_MULT = 3
    ATTACK_BURST_WINDOW = 8

class StateManager:
    def __init__(self):
        self.mid_history = {}
        self.vola_ema = {}
        self.peak_pnl = 0.0
        self.attack_timer = {}
        self.attack_dir = {}
        self.chaotic_timer = 0 # Persistent across all products for global safety

    def update(self, prod: str, mid: float):
        if prod not in self.mid_history:
            self.mid_history[prod] = collections.deque(maxlen=20)
            self.vola_ema[prod] = 0.0
            self.attack_timer[prod] = 0
            self.attack_dir[prod] = 0
        
        last_mid = self.mid_history[prod][-1] if self.mid_history[prod] else mid
        self.vola_ema[prod] = Config.VOLA_EMA_ALPHA * abs(mid - last_mid) + (1.0 - Config.VOLA_EMA_ALPHA) * self.vola_ema[prod]
        self.mid_history[prod].append(mid)
        if self.attack_timer[prod] > 0: self.attack_timer[prod] -= 1
        if self.chaotic_timer > 0: self.chaotic_timer -= 1

class Trader:
    POSITION_LIMITS = Config.POSITION_LIMITS
    DEFAULT_POSITION_LIMIT = 20

    def __init__(self):
        self.state = StateManager()
        self.cumulative_pnl = 0.0

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result = {}
        # Simple PnL tracker (Note: ideally this comes from platform reports)
        # For this version, we focus on the logic stability.
        
        for prod, depth in state.order_depths.items():
            orders = []
            pos = state.position.get(prod, 0)
            limit = self.POSITION_LIMITS.get(prod, Config.DEFAULT_LIMIT)
            
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
            if not best_bid or not best_ask: continue
            
            mid = (best_bid + best_ask) / 2.0
            self.state.update(prod, mid)
            
            # 1. Indicators
            vwap, b_vol, a_vol = self._get_ob_stats(depth)
            imb = (b_vol - a_vol) / (b_vol + a_vol + 1e-9)
            
            h = self.state.mid_history[prod]
            trend_str = abs(h[-1] - h[-11]) if len(h) >= 11 else 0.0
            micro_trend = h[-1] - h[-4] if len(h) >= 4 else 0.0
            vol = self.state.vola_ema[prod]
            
            # 2. Regime Classification
            if vol > Config.CHAOTIC_THRESHOLD: 
                self.state.chaotic_timer = Config.CHAOTIC_COOLDOWN
            
            in_chaotic = self.state.chaotic_timer > 0
            
            if in_chaotic: regime = "CHAOTIC"
            elif trend_str > Config.TREND_STRENGTH_THRESHOLD: regime = "TREND"
            else: regime = "MEAN_REVERT"
            
            # 3. Fair Price & Edge
            fair = (Config.MID_WEIGHT * mid + Config.VWAP_WEIGHT * vwap) if prod != "EMERALDS" else 10000.0
            edge_buy = fair - best_ask
            edge_sell = best_bid - fair
            no_trade = abs(fair - mid) < Config.NO_TRADE_ZONE
            
            # 4. Sizing Logic (Edge Scaled)
            edge = max(edge_buy, edge_sell)
            if edge > 1.8: mult = 3
            elif edge > 1.2: mult = 2
            else: mult = 1
            
            if regime == "CHAOTIC": mult = 0.5
            size = int(Config.BASE_SIZE * mult)
            
            # 5. Execution State & Attack Trigger
            attack_b = edge_buy > Config.AGGR_EDGE_TH and imb > 0.2 and regime == "TREND" and micro_trend > 0
            attack_s = edge_sell > Config.AGGR_EDGE_TH and imb < -0.2 and regime == "TREND" and micro_trend < 0
            
            if attack_b:
                self.state.attack_timer[prod] = Config.ATTACK_BURST_WINDOW
                self.state.attack_dir[prod] = 1
            elif attack_s:
                self.state.attack_timer[prod] = Config.ATTACK_BURST_WINDOW
                self.state.attack_dir[prod] = -1
                
            in_attack = self.state.attack_timer[prod] > 0
            
            # 6. Order Placement
            # BASE MM (Always on but cautious)
            if not no_trade:
                # Widening spread penalty in Chaotic regime
                spread_adj = 1 if not in_chaotic else 2
                
                bid_px = best_bid if edge_buy > 0.1 else best_bid - spread_adj
                ask_px = best_ask if edge_sell > 0.1 else best_ask + spread_adj
                
                # SKEW for flattening
                skew = -int(math.copysign((pos / limit) ** 2 * 3, pos)) if prod != "EMERALDS" else 0
                bid_px += skew; ask_px += skew
                
                can_b = limit - pos
                can_s = -(limit + pos)
                
                # Market Making Orders
                if can_b > 0 and regime != "CHAOTIC":
                    orders.append(Order(prod, int(bid_px), min(can_b, int(Config.BASE_SIZE * 0.5))))
                if can_s < 0 and regime != "CHAOTIC":
                    orders.append(Order(prod, int(ask_px), max(can_s, -int(Config.BASE_SIZE * 0.5))))
            
            # 7. Attack Execution
            if in_attack and regime != "CHAOTIC":
                a_dir = self.state.attack_dir[prod]
                a_size = size * Config.ATTACK_SIZE_MULT
                if a_dir == 1 and edge_buy > 0:
                    orders.append(Order(prod, best_ask, min(limit-pos, a_size)))
                elif a_dir == -1 and edge_sell > 0:
                    orders.append(Order(prod, best_bid, max(-(limit+pos), -a_size)))

            # 8. Strict Position Guard
            if abs(pos) > limit * Config.LIQUIDATION_THRESHOLD:
                orders = [] # Clear and flatten
                if pos > 0: orders.append(Order(prod, best_bid, -pos))
                else: orders.append(Order(prod, best_ask, abs(pos)))

            result[prod] = orders

        return result, 0, state.traderData

    def _get_ob_stats(self, depth: OrderDepth) -> Tuple[float, float, float]:
        b = sorted(depth.buy_orders.items(), reverse=True)[:3]
        s = sorted(depth.sell_orders.items())[:3]
        if not b or not s: return 0.0, 0.0, 0.0
        t_val = 0; t_vol = 0; bv = 0; sv = 0
        for p, v in b: t_val += p * v; t_vol += v; bv += v
        for p, v in s: av = abs(v); t_val += p * av; t_vol += av; sv += av
        return t_val / t_vol if t_vol > 0 else 0.0, bv, sv
