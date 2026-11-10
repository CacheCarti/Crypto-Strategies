from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List

class RobustRsiDivergence(Strategy):
    METADATA = {
        "name": "Robust RSI Divergence ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 3
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _calculate_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsi_values = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0.0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_values.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_values.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_values

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsi_series = self._calculate_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]

        # Manage open position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_rsi >= 58.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_rsi_normalized", "rsi": current_rsi})
            if direction == "short" and current_rsi <= 42.0:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_rsi_normalized", "rsi": current_rsi})
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        w = self.pivot_window
        aligned_closes = closes[-len(rsi_series):]
        low_pivots = []
        high_pivots = []

        # Find pivot extrema within lookback window
        for i in range(w, len(rsi_series) - w):
            # Pivot Low
            if (aligned_closes[i] <= min(aligned_closes[i - w:i]) and 
                aligned_closes[i] <= min(aligned_closes[i + 1:i + w + 1])):
                low_pivots.append((i, aligned_closes[i], rsi_series[i]))

            # Pivot High
            if (aligned_closes[i] >= max(aligned_closes[i - w:i]) and 
                aligned_closes[i] >= max(aligned_closes[i + 1:i + w + 1])):
                high_pivots.append((i, aligned_closes[i], rsi_series[i]))

        # Bullish Divergence check (Lower low in price, higher low in RSI)
        if len(low_pivots) >= 2:
            prev_p, curr_p = low_pivots[-2], low_pivots[-1]
            if curr_p[0] >= len(rsi_series) - (w + 2):
                if curr_p[1] < prev_p[1] and curr_p[2] > prev_p[2] and curr_p[2] < 42.0:
                    if current_rsi > curr_p[2] and current_rsi < 48.0:
                        return ctx.signal(
                            "long",
                            confidence=0.78,
                            stop_loss_bps=280.0,
                            take_profit_bps=420.0,
                            horizon_seconds=18000,
                            metadata={
                                "reason": "bullish_rsi_divergence",
                                "current_rsi": current_rsi,
                                "p1_rsi": prev_p[2],
                                "p2_rsi": curr_p[2],
                                "p1_price": prev_p[1],
                                "p2_price": curr_p[1]
                            }
                        )

        # Bearish Divergence check (Higher high in price, lower high in RSI)
        if len(high_pivots) >= 2:
            prev_p, curr_p = high_pivots[-2], high_pivots[-1]
            if curr_p[0] >= len(rsi_series) - (w + 2):
                if curr_p[1] > prev_p[1] and curr_p[2] < prev_p[2] and curr_p[2] > 58.0:
                    if current_rsi < curr_p[2] and current_rsi > 52.0:
                        return ctx.signal(
                            "short",
                            confidence=0.78,
                            stop_loss_bps=280.0,
                            take_profit_bps=420.0,
                            horizon_seconds=18000,
                            metadata={
                                "reason": "bearish_rsi_divergence",
                                "current_rsi": current_rsi,
                                "p1_rsi": prev_p[2],
                                "p2_rsi": curr_p[2],
                                "p1_price": prev_p[1],
                                "p2_price": curr_p[1]
                            }
                        )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index