from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsi_series = [50.0] * period
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0.0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def _find_pivot_lows(self, lows: list, rsis: list, window: int = 2):
        pivots = []
        n = len(lows)
        for i in range(window, n - window):
            val = lows[i]
            is_pivot = True
            for k in range(1, window + 1):
                if lows[i - k] <= val or lows[i + k] < val:
                    is_pivot = False
                    break
            if is_pivot:
                pivots.append((i, lows[i], rsis[i]))
        return pivots

    def _find_pivot_highs(self, highs: list, rsis: list, window: int = 2):
        pivots = []
        n = len(highs)
        for i in range(window, n - window):
            val = highs[i]
            is_pivot = True
            for k in range(1, window + 1):
                if highs[i - k] >= val or highs[i + k] > val:
                    is_pivot = False
                    break
            if is_pivot:
                pivots.append((i, highs[i], rsis[i]))
        return pivots

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 6)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        highs = ctx.highs(self.lookback + self.rsi_period + 6)
        lows = ctx.lows(self.lookback + self.rsi_period + 6)
        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < len(closes):
            return None

        current_rsi = rsi_series[-1]

        # Manage existing position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            pos_dir = ctx.position_direction()

            # Time-based stop
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_normalized", "bars_held": bars_held, "rsi": round(current_rsi, 2)}
                )

            # RSI normalization exit
            if pos_dir == "long" and current_rsi >= 55.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_exit_long", "rsi": round(current_rsi, 2), "bars_held": bars_held}
                )
            elif pos_dir == "short" and current_rsi <= 45.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_reversion_exit_short", "rsi": round(current_rsi, 2), "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown between trades
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Detect pivots within recent lookback
        eval_lows = lows[-self.lookback:]
        eval_highs = highs[-self.lookback:]
        eval_rsi = rsi_series[-self.lookback:]

        pivot_lows = self._find_pivot_lows(eval_lows, eval_rsi, window=2)
        pivot_highs = self._find_pivot_highs(eval_highs, eval_rsi, window=2)

        # Bullish Divergence: Lower Low in Price, Higher Low in RSI (Recent pivot near oversold)
        if len(pivot_lows) >= 2:
            prev_idx, prev_low, prev_rsi = pivot_lows[-2]
            curr_idx, curr_low, curr_rsi = pivot_lows[-1]

            if (self.lookback - 1 - curr_idx) <= 4:
                if curr_low < prev_low and curr_rsi > prev_rsi and prev_rsi < 36.0 and curr_rsi < 44.0:
                    self.entry_bar = ctx.bar_index
                    conf = min(0.9, max(0.6, 0.6 + (curr_rsi - prev_rsi) * 0.02))
                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "curr_low": curr_low,
                            "prev_low": prev_low,
                            "curr_rsi": round(curr_rsi, 2),
                            "prev_rsi": round(prev_rsi, 2),
                        }
                    )

        # Bearish Divergence: Higher High in Price, Lower High in RSI (Recent pivot near overbought)
        if len(pivot_highs) >= 2:
            prev_idx, prev_high, prev_rsi = pivot_highs[-2]
            curr_idx, curr_high, curr_rsi = pivot_highs[-1]

            if (self.lookback - 1 - curr_idx) <= 4:
                if curr_high > prev_high and curr_rsi < prev_rsi and prev_rsi > 64.0 and curr_rsi > 56.0:
                    self.entry_bar = ctx.bar_index
                    conf = min(0.9, max(0.6, 0.6 + (prev_rsi - curr_rsi) * 0.02))
                    return ctx.signal(
                        "short",
                        confidence=conf,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "rsi": round(current_rsi, 2),
                            "curr_high": curr_high,
                            "prev_high": prev_high,
                            "curr_rsi": round(curr_rsi, 2),
                            "prev_rsi": round(prev_rsi, 2),
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index