from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class EthRsiDivergence(Strategy):
    METADATA = {
        "name": "EthRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing hold
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _calc_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        n = len(closes)
        if n < period + 1:
            return []
        
        gains = []
        losses = []
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        rsi_series = []
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)

        if len(closes) < self.lookback + self.rsi_period:
            return None

        # Manage active position exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            rsi_vals = self._calc_rsi_series(closes, self.rsi_period)
            curr_rsi = rsi_vals[-1] if rsi_vals else 50.0

            # Time stop exit (8 bars ~ 8h)
            if bars_held >= 8:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_reached", "bars_held": bars_held, "rsi": curr_rsi}
                )

            # RSI normalization exit
            if pos_dir == "long" and curr_rsi >= 55.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_normalized_long", "rsi": curr_rsi, "bars_held": bars_held}
                )
            elif pos_dir == "short" and curr_rsi <= 45.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "rsi_normalized_short", "rsi": curr_rsi, "bars_held": bars_held}
                )

            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        # Look for pivot lows and pivot highs in the last lookback window
        # Pivot low: lower than neighbors (k-1, k+1)
        # We search from window_start to window_end (excluding current bar)
        recent_lows = lows[-self.lookback:]
        recent_highs = highs[-self.lookback:]
        recent_rsi = rsi_series[-self.lookback:]

        # Find bullish divergence:
        # Prior pivot low has lower RSI and higher/equal price, current pivot low has lower price and higher RSI
        pivot_lows = []
        for i in range(2, len(recent_lows) - 2):
            if (recent_lows[i] < recent_lows[i - 1] and recent_lows[i] < recent_lows[i - 2] and
                recent_lows[i] < recent_lows[i + 1] and recent_lows[i] < recent_lows[i + 2]):
                if recent_rsi[i] < 42.0:
                    pivot_lows.append((i, recent_lows[i], recent_rsi[i]))

        # Find bearish divergence:
        # Prior pivot high has higher RSI, current pivot high has higher price and lower RSI
        pivot_highs = []
        for i in range(2, len(recent_highs) - 2):
            if (recent_highs[i] > recent_highs[i - 1] and recent_highs[i] > recent_highs[i - 2] and
                recent_highs[i] > recent_highs[i + 1] and recent_highs[i] > recent_highs[i + 2]):
                if recent_rsi[i] > 58.0:
                    pivot_highs.append((i, recent_highs[i], recent_rsi[i]))

        curr_close = closes[-1]
        curr_low = lows[-1]
        curr_high = highs[-1]
        curr_rsi = recent_rsi[-1]

        # Bullish Divergence Entry Check
        if len(pivot_lows) >= 1 and curr_rsi < 40.0:
            last_p_idx, last_p_low, last_p_rsi = pivot_lows[-1]
            bars_since = (len(recent_lows) - 1) - last_p_idx
            # Pivot formed between 4 and 24 bars ago
            if 4 <= bars_since <= 24:
                if curr_low < last_p_low and curr_rsi > last_p_rsi:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bullish_rsi_divergence",
                            "curr_rsi": curr_rsi,
                            "pivot_rsi": last_p_rsi,
                            "curr_low": curr_low,
                            "pivot_low": last_p_low,
                            "bars_since_pivot": bars_since,
                        }
                    )

        # Bearish Divergence Entry Check
        if len(pivot_highs) >= 1 and curr_rsi > 60.0:
            last_p_idx, last_p_high, last_p_rsi = pivot_highs[-1]
            bars_since = (len(recent_highs) - 1) - last_p_idx
            # Pivot formed between 4 and 24 bars ago
            if 4 <= bars_since <= 24:
                if curr_high > last_p_high and curr_rsi < last_p_rsi:
                    self.entry_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "bearish_rsi_divergence",
                            "curr_rsi": curr_rsi,
                            "pivot_rsi": last_p_rsi,
                            "curr_high": curr_high,
                            "pivot_high": last_p_high,
                            "bars_since_pivot": bars_since,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index