from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List, Tuple
import math

class RsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "RsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours target hold
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_trade = 0
        self.max_hold_bars = 16

    def _calc_rsi_series(self, closes: List[float], period: int = 14) -> List[float]:
        if len(closes) < period + 1:
            return []
        
        rsi_series: List[float] = []
        gains: List[float] = []
        losses: List[float] = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        # Wilder's smoothing
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

    def _find_pivots(self, highs: List[float], lows: List[float], rsis: List[float]) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
        # Search for recent swing points (3-bar local extrema) in the last lookback bars
        n = len(rsis)
        if n < self.lookback:
            return None, None

        bull_pair = None
        bear_pair = None

        # Detect local trough pivots for bullish divergence
        troughs = []
        for i in range(n - self.lookback + 2, n - 1):
            if lows[i] <= lows[i - 1] and lows[i] < lows[i + 1]:
                troughs.append(i)

        if len(troughs) >= 2:
            p1, p2 = troughs[-2], troughs[-1]
            if (p2 - p1) >= 4:
                # Bullish divergence: price lower low, RSI higher low, recent pivot oversold
                if lows[p2] < lows[p1] and rsis[p2] > rsis[p1] and rsis[p2] < 42.0:
                    bull_pair = (p1, p2)

        # Detect local peak pivots for bearish divergence
        peaks = []
        for i in range(n - self.lookback + 2, n - 1):
            if highs[i] >= highs[i - 1] and highs[i] > highs[i + 1]:
                peaks.append(i)

        if len(peaks) >= 2:
            p1, p2 = peaks[-2], peaks[-1]
            if (p2 - p1) >= 4:
                # Bearish divergence: price higher high, RSI lower high, recent pivot overbought
                if highs[p2] > highs[p1] and rsis[p2] < rsis[p1] and rsis[p2] > 58.0:
                    bear_pair = (p1, p2)

        return bull_pair, bear_pair

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        highs = ctx.highs(self.lookback + self.rsi_period + 5)
        lows = ctx.lows(self.lookback + self.rsi_period + 5)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Compute full RSI aligned to bars
        rsi_series = self._calc_rsi_series(closes, self.rsi_period)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Handle position maintenance & dynamic exits
        if has_pos:
            self.bars_in_trade += 1
            # Normalization exits or time-stop
            if pos_dir == "long":
                if current_rsi >= 55.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_normalized_long", "rsi": current_rsi})
            elif pos_dir == "short":
                if current_rsi <= 45.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_normalized_short", "rsi": current_rsi})

            if self.bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_stop_exhaustion", "bars": self.bars_in_trade})

            return None
        else:
            self.bars_in_trade = 0

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter extreme crisis regimes
        crisis = ctx.market.get("crisis_score", 0.0)
        if crisis > 0.65:
            return None

        # Scan for divergences
        # Align highs/lows with the rsi_series slice
        offset = len(highs) - len(rsi_series)
        aligned_highs = highs[offset:]
        aligned_lows = lows[offset:]

        bull_div, bear_div = self._find_pivots(aligned_highs, aligned_lows, rsi_series)

        # Trigger bullish entry on recent pivot confirmation
        if bull_div is not None:
            _, p2 = bull_div
            # Ensure pivot 2 is very fresh (formed 1-2 bars ago)
            if (len(rsi_series) - 1 - p2) <= 2 and current_rsi > rsi_series[p2]:
                confidence = min(0.85, 0.60 + (45.0 - current_rsi) * 0.01)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "bullish_rsi_divergence",
                        "rsi": current_rsi,
                        "p1_rsi": rsi_series[bull_div[0]],
                        "p2_rsi": rsi_series[p2],
                        "price": ctx.bar.close,
                    },
                )

        # Trigger bearish entry on recent pivot confirmation
        if bear_div is not None:
            _, p2 = bear_div
            if (len(rsi_series) - 1 - p2) <= 2 and current_rsi < rsi_series[p2]:
                confidence = min(0.85, 0.60 + (current_rsi - 55.0) * 0.01)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "bearish_rsi_divergence",
                        "rsi": current_rsi,
                        "p1_rsi": rsi_series[bear_div[0]],
                        "p2_rsi": rsi_series[p2],
                        "price": ctx.bar.close,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0