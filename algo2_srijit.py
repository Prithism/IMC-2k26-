import json
from typing import Dict, List, Optional, Tuple

from datamodel import Order, OrderDepth, TradingState


class Trader:
    """
    Round 1 baseline:
    - Compute best bid/ask and mid each tick.
    - Maintain a simple EMA of mid as an estimate of "fair".
    - Mean reversion: take liquidity when price is far from fair.
    - Light market making: when spread is wide, place small passive quotes.

    Notes:
    - Orders are single-iteration: anything not filled is cancelled by the exchange.
    - We always clamp order sizes so we never exceed position limits.
    """

    POSITION_LIMITS: Dict[str, int] = {"TOMATOES": 250, "EMERALDS": 20}
    DEFAULT_POSITION_LIMIT = 20

    # EMA smoothing for fair value
    EMA_ALPHA = 0.2

    # Minimum spread to consider trading/quoting (avoid overtrading)
    MIN_SPREAD_TO_TRADE = 2

    # Threshold (in ticks) away from fair to take liquidity
    EDGE_TO_TAKE = 2

    # How much size to use for passive quoting (kept small and safe)
    QUOTE_SIZE = 5

    def __init__(self) -> None:
        self.ema_fair: Dict[str, float] = {}

    @staticmethod
    def _best_bid_ask(depth: OrderDepth) -> Tuple[Optional[int], Optional[int]]:
        best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else None
        best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else None
        return best_bid, best_ask

    @staticmethod
    def _clamp_buy_qty(current_pos: int, limit: int, desired_qty: int) -> int:
        """Return a buy quantity clamped so current_pos + qty <= limit."""
        if desired_qty <= 0:
            return 0
        return max(0, min(desired_qty, limit - current_pos))

    @staticmethod
    def _clamp_sell_qty(current_pos: int, limit: int, desired_qty: int) -> int:
        """Return a sell quantity (positive magnitude) clamped so current_pos - qty >= -limit."""
        if desired_qty <= 0:
            return 0
        return max(0, min(desired_qty, current_pos + limit))

    def run(self, state: TradingState):
        # Keep traderData minimal: we only store our EMA fair values.
        if state.traderData:
            try:
                data = json.loads(state.traderData)
                ema = data.get("ema_fair", {})
                if isinstance(ema, dict):
                    self.ema_fair = {k: float(v) for k, v in ema.items()}
            except Exception:
                # If traderData is malformed, just ignore and continue safely.
                self.ema_fair = self.ema_fair

        result: Dict[str, List[Order]] = {}

        for product, depth in state.order_depths.items():
            orders: List[Order] = []
            pos = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, self.DEFAULT_POSITION_LIMIT)

            best_bid, best_ask = self._best_bid_ask(depth)
            if best_bid is None or best_ask is None:
                # If one side is missing, we cannot compute a robust mid.
                print(f"[{product}] Empty/one-sided book. best_bid={best_bid} best_ask={best_ask}. No trade.")
                result[product] = orders
                continue

            mid = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid

            # Update EMA fair estimate
            prev_fair = self.ema_fair.get(product, mid)
            fair = (self.EMA_ALPHA * mid) + ((1.0 - self.EMA_ALPHA) * prev_fair)
            self.ema_fair[product] = fair

            print(f"[{product}] best_bid={best_bid} best_ask={best_ask} mid={mid:.1f} fair={fair:.1f} spread={spread} pos={pos}")

            # Avoid overtrading in tight markets
            if spread < self.MIN_SPREAD_TO_TRADE:
                print(f"[{product}] Decision: skip (spread {spread} < {self.MIN_SPREAD_TO_TRADE})")
                result[product] = orders
                continue

            # --- Mean reversion: take liquidity when there's a clear edge ---
            # If ask is cheap vs fair, buy at best ask.
            if best_ask <= fair - self.EDGE_TO_TAKE:
                available_at_ask = -depth.sell_orders.get(best_ask, 0)  # sell volumes are negative
                buy_qty = self._clamp_buy_qty(pos, limit, min(available_at_ask, limit))
                if buy_qty > 0:
                    orders.append(Order(product, best_ask, buy_qty))
                    print(f"[{product}] Decision: BUY {buy_qty} @ {best_ask} (ask below fair by {fair - best_ask:.1f})")

            # If bid is expensive vs fair, sell at best bid.
            if best_bid >= fair + self.EDGE_TO_TAKE:
                available_at_bid = depth.buy_orders.get(best_bid, 0)
                sell_qty = self._clamp_sell_qty(pos, limit, min(available_at_bid, limit))
                if sell_qty > 0:
                    orders.append(Order(product, best_bid, -sell_qty))
                    print(f"[{product}] Decision: SELL {sell_qty} @ {best_bid} (bid above fair by {best_bid - fair:.1f})")

            # --- Light market making: if we didn't take, place small passive quotes ---
            # Quote around fair but stay inside the current spread so we don't cross unnecessarily.
            if not orders and spread >= self.MIN_SPREAD_TO_TRADE + 2:
                bid_px = min(int(fair - 1), best_ask - 1)
                ask_px = max(int(fair + 1), best_bid + 1)

                buy_qty = self._clamp_buy_qty(pos, limit, self.QUOTE_SIZE)
                sell_qty = self._clamp_sell_qty(pos, limit, self.QUOTE_SIZE)

                if buy_qty > 0 and bid_px <= best_bid:
                    orders.append(Order(product, bid_px, buy_qty))
                    print(f"[{product}] Decision: MAKE bid {buy_qty} @ {bid_px}")
                if sell_qty > 0 and ask_px >= best_ask:
                    orders.append(Order(product, ask_px, -sell_qty))
                    print(f"[{product}] Decision: MAKE ask {sell_qty} @ {ask_px}")

                if not orders:
                    print(f"[{product}] Decision: no safe passive quotes (bid_px={bid_px}, ask_px={ask_px})")

            result[product] = orders

        traderData = json.dumps({"ema_fair": self.ema_fair})
        conversions = 0
        return result, conversions, traderData
