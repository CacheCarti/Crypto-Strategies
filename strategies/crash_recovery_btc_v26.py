from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.045  # 4.5% drop in 24h to avoid zero-trade rejection
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_close = ctx.bar.close

        # Manage existing long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Midpoint take profit or time stop
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_flush_midpoint", "close": current_close, "target": self.target_midpoint}
                )
            if bars_held >= 12:  # Max 12 bars (12h) hold before timed exit
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "max_time_elapsed", "bars_held": bars_held, "close": current_close}
                )
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate flush across the rolling 24-bar window
        highest_24 = max(highs[-self.lookback:])
        lowest_24 = min(lows[-self.lookback:])
        peak_to_trough_drop = (highest_24 - lowest_24) / highest_24

        # Condition 1: Significant flush has occurred
        if peak_to_trough_drop < self.drop_threshold:
            return None

        # Condition 2: Capitulation reversal candle (closes back above previous high)
        prev_high = highs[-2]
        is_reversal = current_close > prev_high

        # RSI filter: oversold condition rebounding, avoiding dead-cat freefalls
        rsi_val = self._rsi(closes, period=14)
        rsi_valid = rsi_val < 42.0

        if is_reversal and rsi_valid:
            midpoint = (highest_24 + lowest_24) / 2.0
            # Target must be above current close to justify entry
            if midpoint <= current_close:
                return None

            self.target_midpoint = midpoint
            self.entry_bar = ctx.bar_index

            confidence = min(0.9, max(0.5, peak_to_trough_drop * 10.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "flush_capitulation_recovery",
                    "drop_pct": round(peak_to_trough_drop * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "prev_high": prev_high,
                    "close": current_close,
                    "target_midpoint": round(midpoint, 2),
                }
            )

        return None