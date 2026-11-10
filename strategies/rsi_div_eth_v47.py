from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class RsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "RsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 2
        self.cooldown_bars = 6
        self.max_hold_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _rsi_series(self, closes, period=14):
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsis = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 100.0
        rsis.append(100.0 - (100.0 / (1.0 + rs)) if avg_loss != 0 else 100.0)

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100.0 - (100.0 / (1.0 + rs)))
        return rsis

    def _find_pivots(self, series, is_low=True, window=2):
        pivots = []
        n = len(series)
        for i in range(window, n - window):
            val = series[i]
            if is_low:
                if all(val <= series[i - k] for k in range(1, window + 1)) and \
                   all(val < series[i + k] for k in range(1, window + 1)):
                    pivots.append((i, val))
            else:
                if all(val >= series[i - k] for k in range(1, window + 1)) and \
                   all(val > series[i + k] for k in range(1, window + 1)):
                    pivots.append((i, val))
        return pivots

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period + 5:
            return None

        rsi_vals = self._rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback:
            return None

        curr_rsi = rsi_vals[-1]
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Position Management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Structural normalization exits
            if pos_dir == "long" and curr_rsi >= 58.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_normalized_long", "rsi": curr_rsi})
            elif pos_dir == "short" and curr_rsi <= 42.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_normalized_short", "rsi": curr_rsi})

            # Time stop to free capital
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_stop_expired", "bars_held": bars_held, "rsi": curr_rsi})

            return None

        if bars_since_exit < self.cooldown_bars:
            return None

        # Filter crisis regime
        if ctx.regime == "crisis":
            return None

        recent_closes = closes[-self.lookback:]
        recent_rsi = rsi_vals[-self.lookback:]

        low_pivots = self._find_pivots(recent_closes, is_low=True, window=self.pivot_window)
        if len(low_pivots) >= 2:
            idx1, price1 = low_pivots[-2]
            idx2, price2 = low_pivots[-1]
            # Must be a recent confirmed pivot
            if (len(recent_closes) - 1 - idx2) <= 3 and (idx2 - idx1) >= 4:
                rsi1 = recent_rsi[idx1]
                rsi2 = recent_rsi[idx2]
                # Bullish divergence: price lower low, RSI higher low, deep value RSI
                if price2 < price1 and rsi2 > (rsi1 + 1.5) and rsi2 < 42.0:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=220.0,
                        take_profit_bps=380.0,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "price_pivot1": price1,
                            "price_pivot2": price2,
                            "rsi_pivot1": rsi1,
                            "rsi_pivot2": rsi2,
                            "rsi": curr_rsi,
                        }
                    )

        high_pivots = self._find_pivots(recent_closes, is_low=False, window=self.pivot_window)
        if len(high_pivots) >= 2:
            idx1, price1 = high_pivots[-2]
            idx2, price2 = high_pivots[-1]
            if (len(recent_closes) - 1 - idx2) <= 3 and (idx2 - idx1) >= 4:
                rsi1 = recent_rsi[idx1]
                rsi2 = recent_rsi[idx2]
                # Bearish divergence: price higher high, RSI lower high, elevated RSI
                if price2 > price1 and rsi2 < (rsi1 - 1.5) and rsi2 > 58.0:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=220.0,
                        take_profit_bps=380.0,
                        horizon_seconds=21600,
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "price_pivot1": price1,
                            "price_pivot2": price2,
                            "rsi_pivot1": rsi1,
                            "rsi_pivot2": rsi2,
                            "rsi": curr_rsi,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index