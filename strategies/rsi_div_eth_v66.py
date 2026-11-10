from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AdvancedRsiDivergence(Strategy):
    METADATA = {
        "name": "AdvancedRsiDivergence",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 32
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16

    def _calc_rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _find_rsi_series(self, closes: list, count: int, period: int = 14) -> list:
        series = []
        for i in range(len(closes) - count + 1, len(closes) + 1):
            val = self._calc_rsi(closes[:i], period)
            series.append(val if val is not None else 50.0)
        return series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period + 5:
            return None

        current_rsi = self._calc_rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            direction = ctx.position_direction()

            # Mean-reversion normalization exit or time stop
            if direction == "long" and (current_rsi >= 58.0 or bars_held >= self.max_hold_bars):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_long", "rsi": current_rsi, "bars_held": bars_held})
            elif direction == "short" and (current_rsi <= 42.0 or bars_held >= self.max_hold_bars):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_short", "rsi": current_rsi, "bars_held": bars_held})
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Calculate rolling RSI series over the lookback window
        rsi_series = self._find_rsi_series(closes, self.lookback, self.rsi_period)
        lows = ctx.lows(self.lookback)
        highs = ctx.highs(self.lookback)

        # Detect swing pivots across two distinct halves of the lookback window
        split = self.lookback // 2
        p1_low, p2_low = min(lows[:split]), min(lows[split:])
        p1_rsi_low, p2_rsi_low = min(rsi_series[:split]), min(rsi_series[split:])

        p1_high, p2_high = max(highs[:split]), max(highs[split:])
        p1_rsi_high, p2_rsi_high = max(rsi_series[:split]), max(rsi_series[split:])

        # Bullish Divergence: Price lower low, RSI higher low, recent RSI in oversold rebound territory
        bullish_div = (p2_low < p1_low) and (p2_rsi_low > p1_rsi_low + 2.0) and (current_rsi < 44.0) and (p2_rsi_low < 38.0)

        # Bearish Divergence: Price higher high, RSI lower high, recent RSI in overbought rejection territory
        bearish_div = (p2_high > p1_high) and (p2_rsi_high < p1_rsi_high - 2.0) and (current_rsi > 56.0) and (p2_rsi_high > 62.0)

        if bullish_div:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "rsi": current_rsi,
                    "p1_low": p1_low,
                    "p2_low": p2_low,
                    "p1_rsi": p1_rsi_low,
                    "p2_rsi": p2_rsi_low,
                }
            )

        if bearish_div:
            self.entry_bar = ctx.bar_index
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "rsi": current_rsi,
                    "p1_high": p1_high,
                    "p2_high": p2_high,
                    "p1_rsi": p1_rsi_high,
                    "p2_rsi": p2_rsi_high,
                }
            )

        return None