from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryBTC(Strategy):
    METADATA = {
        "name": "CrashRecoveryBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_drop_pct = 0.050  # 5.0% drop over 24 bars to capture genuine flushes cleanly
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.target_midpoint = 0.0
        self.max_hold_bars = 18

    def _rsi(self, closes, period=14):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_price = ctx.bar.close

            # Target reached (mean reversion to 24-bar crash midpoint)
            if self.target_midpoint > 0.0 and curr_price >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "crash_midpoint_reversion_target_hit",
                    "bars_held": bars_held,
                    "target_midpoint": self.target_midpoint,
                    "close": curr_price
                })

            # Time-based expiration for bounce leg
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "time_stop_flush_recovery_expired",
                    "bars_held": bars_held,
                    "close": curr_price
                })

            return None

        # Entry Logic
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check rolling 24-bar window drawdown
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        current_close = closes[-1]
        prev_high = highs[-2]

        if window_high <= 0:
            return None

        drawdown_pct = (window_high - window_low) / window_high

        # Event trigger: 24h drop >= flush_drop_pct and current bar closes above previous bar's high
        is_flushed = drawdown_pct >= self.flush_drop_pct
        closed_above_prev_high = current_close > prev_high

        if is_flushed and closed_above_prev_high:
            rsi = self._rsi(closes, period=14)
            # Capitulation check: RSI must confirm washed out conditions (e.g. < 48)
            if rsi is not None and rsi < 48.0:
                self.entry_bar = ctx.bar_index
                self.target_midpoint = window_low + (window_high - window_low) * 0.50

                # Scale confidence by depth of crash
                conf = min(0.90, max(0.60, 0.60 + (drawdown_pct - self.flush_drop_pct) * 3.0))

                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "flush_recovery_break_prev_high",
                        "drawdown_pct": round(drawdown_pct, 4),
                        "rsi": round(rsi, 2),
                        "window_high": round(window_high, 2),
                        "window_low": round(window_low, 2),
                        "target_midpoint": round(self.target_midpoint, 2),
                        "prev_high": round(prev_high, 2),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0