from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.pivot_window = 2  # bar needs to be lower/higher than 2 bars on either side
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _calculate_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsi_series = []
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0:
            rsi_series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))

        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n_bars = self.lookback + self.rsi_period + 6
        closes = ctx.closes(n_bars)
        lows = ctx.lows(n_bars)
        highs = ctx.highs(n_bars)

        if len(closes) < n_bars:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.last_entry_bar

            # Rapid RSI normalization exit or time-stop
            rsi_series = self._calculate_rsi_series(closes[-25:], self.rsi_period)
            if rsi_series:
                curr_rsi = rsi_series[-1]
                if pos_dir == "long" and (curr_rsi >= 58.0 or bars_held >= 12):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "long_rsi_normalized_or_timeout", "rsi": curr_rsi, "bars_held": bars_held}
                    )
                elif pos_dir == "short" and (curr_rsi <= 42.0 or bars_held >= 12):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={"reason": "short_rsi_normalized_or_timeout", "rsi": curr_rsi, "bars_held": bars_held}
                    )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None
        if ctx.bar_index - self.last_entry_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid choppy crisis washouts
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi_vals = self._calculate_rsi_series(closes, self.rsi_period)
        if len(rsi_vals) < self.lookback + 3:
            return None

        # Align series for the divergence window
        eval_lows = lows[-self.lookback:]
        eval_highs = highs[-self.lookback:]
        eval_rsi = rsi_vals[-self.lookback:]

        # Detect swing low pivots (local minimum with 2 bars left & right)
        low_pivots = []
        for i in range(self.pivot_window, len(eval_lows) - self.pivot_window):
            is_pivot = True
            for offset in range(1, self.pivot_window + 1):
                if eval_lows[i] >= eval_lows[i - offset] or eval_lows[i] > eval_lows[i + offset]:
                    is_pivot = False
                    break
            if is_pivot:
                low_pivots.append((i, eval_lows[i], eval_rsi[i]))

        # Detect swing high pivots
        high_pivots = []
        for i in range(self.pivot_window, len(eval_highs) - self.pivot_window):
            is_pivot = True
            for offset in range(1, self.pivot_window + 1):
                if eval_highs[i] <= eval_highs[i - offset] or eval_highs[i] < eval_highs[i + offset]:
                    is_pivot = False
                    break
            if is_pivot:
                high_pivots.append((i, eval_highs[i], eval_rsi[i]))

        curr_rsi = eval_rsi[-1]

        # 1. Bullish Divergence Event: Price lower low, RSI higher low on recent pivots
        # Pivot must be fresh (completed within last 3 bars)
        if len(low_pivots) >= 2:
            prev_idx, prev_price_low, prev_rsi_low = low_pivots[-2]
            curr_idx, curr_price_low, curr_rsi_low = low_pivots[-1]
            bars_since_pivot = (len(eval_lows) - 1) - curr_idx

            if bars_since_pivot <= 3 and (curr_idx - prev_idx) >= 5:
                if curr_price_low < prev_price_low and curr_rsi_low > prev_rsi_low:
                    if curr_rsi_low < 42.0 and curr_rsi < 48.0:
                        self.last_entry_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bullish_rsi_divergence",
                                "prev_low": prev_price_low,
                                "curr_low": curr_price_low,
                                "prev_rsi": prev_rsi_low,
                                "curr_rsi": curr_rsi_low,
                                "current_rsi": curr_rsi
                            }
                        )

        # 2. Bearish Divergence Event: Price higher high, RSI lower high on recent pivots
        if len(high_pivots) >= 2:
            prev_idx, prev_price_high, prev_rsi_high = high_pivots[-2]
            curr_idx, curr_price_high, curr_rsi_high = high_pivots[-1]
            bars_since_pivot = (len(eval_highs) - 1) - curr_idx

            if bars_since_pivot <= 3 and (curr_idx - prev_idx) >= 5:
                if curr_price_high > prev_price_high and curr_rsi_high < prev_rsi_high:
                    if curr_rsi_high > 58.0 and curr_rsi > 52.0:
                        self.last_entry_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.75,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "bearish_rsi_divergence",
                                "prev_high": prev_price_high,
                                "curr_high": curr_price_high,
                                "prev_rsi": prev_rsi_high,
                                "curr_rsi": curr_rsi_high,
                                "current_rsi": curr_rsi
                            }
                        )

        return None