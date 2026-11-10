from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class RobustRsiDivergence(Strategy):
    METADATA = {
        "name": "RobustRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 40
        self.pivot_window = 3
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _calculate_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        n = len(closes)
        if n < period + 1:
            return []
        
        rsi_series = []
        gains = [max(closes[i] - closes[i-1], 0.0) for i in range(1, period + 1)]
        losses = [max(closes[i-1] - closes[i], 0.0) for i in range(1, period + 1)]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
        rsi_series.append(100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + rs)))

        for i in range(period + 1, n):
            gain = max(closes[i] - closes[i-1], 0.0)
            loss = max(closes[i-1] - closes[i], 0.0)
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
            rsi = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + rs))
            rsi_series.append(rsi)
            
        return rsi_series

    def _find_pivots(self, prices: List[float], rsis: List[float]) -> Tuple[List[Tuple[int, float, float]], List[Tuple[int, float, float]]]:
        w = self.pivot_window
        lows = []
        highs = []
        total = len(prices)
        for i in range(w, total - w):
            p = prices[i]
            if all(p < prices[i - j] for j in range(1, w + 1)) and all(p <= prices[i + j] for j in range(1, w + 1)):
                lows.append((i, p, rsis[i]))
            if all(p > prices[i - j] for j in range(1, w + 1)) and all(p >= prices[i + j] for j in range(1, w + 1)):
                highs.append((i, p, rsis[i]))
        return lows, highs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n = self.lookback + self.pivot_window * 2 + 16
        closes = ctx.closes(n)
        if len(closes) < n:
            return None

        rsi_series = self._calculate_rsi_series(closes, period=14)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        aligned_closes = closes[-len(rsi_series):]

        # Manage open position exits
        if ctx.has_position():
            pos = ctx.position
            direction = pos.get("direction") if pos else None
            bars_held = ctx.bar_index - self.entry_bar
            
            if direction == "long" and (current_rsi >= 62.0 or bars_held >= 24):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_normalized_or_time_exit", "rsi": current_rsi, "bars_held": bars_held})
            elif direction == "short" and (current_rsi <= 38.0 or bars_held >= 24):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "rsi_normalized_or_time_exit", "rsi": current_rsi, "bars_held": bars_held})
            return None

        # Check post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Check market stress / extreme crisis filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.75:
            return None

        window_closes = aligned_closes[-self.lookback:]
        window_rsi = rsi_series[-self.lookback:]
        low_pivots, high_pivots = self._find_pivots(window_closes, window_rsi)

        # Bullish Divergence Detection: Lower price low, higher RSI low (both oversold territory)
        if len(low_pivots) >= 2:
            p1 = low_pivots[-2]
            p2 = low_pivots[-1]
            idx_diff = p2[0] - p1[0]
            if 4 <= idx_diff <= 25 and p2[0] >= len(window_closes) - (self.pivot_window + 4):
                if p2[1] < p1[1] and p2[2] > p1[2] and p1[2] < 40.0 and current_rsi > p2[2]:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.72,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": current_rsi,
                            "p1_price": p1[1],
                            "p2_price": p2[1],
                            "p1_rsi": p1[2],
                            "p2_rsi": p2[2],
                        }
                    )

        # Bearish Divergence Detection: Higher price high, lower RSI high (both overbought territory)
        if len(high_pivots) >= 2:
            p1 = high_pivots[-2]
            p2 = high_pivots[-1]
            idx_diff = p2[0] - p1[0]
            if 4 <= idx_diff <= 25 and p2[0] >= len(window_closes) - (self.pivot_window + 4):
                if p2[1] > p1[1] and p2[2] < p1[2] and p1[2] > 60.0 and current_rsi < p2[2]:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.72,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": current_rsi,
                            "p1_price": p1[1],
                            "p2_price": p2[1],
                            "p1_rsi": p1[2],
                            "p2_rsi": p2[2],
                        }
                    )

        return None