from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 72,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.panic_low = 0.0
        self.panic_high = 0.0
        self.target_midpoint = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(72)
        highs = ctx.highs(72)
        lows = ctx.lows(72)

        if len(closes) < 72:
            return None

        current_close = ctx.bar.close

        # Position Management
        if ctx.has_position():
            # Exit if reached stabilization flush midpoint
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "close": current_close,
                        "target_midpoint": self.target_midpoint,
                        "panic_low": self.panic_low
                    }
                )

            # Exit if price breaks below the confirmed panic swing low
            if self.panic_low > 0.0 and current_close < self.panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "flush_structural_low_broken",
                        "close": current_close,
                        "panic_low": self.panic_low
                    }
                )

            return None

        # Mandatory cooldown between trades to eliminate overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Economic Mechanism: Quantify flush magnitude over the lookback window
        window_high = max(highs[-self.lookback_bars:])
        window_low = min(lows[-self.lookback_bars:])
        
        if window_high <= 0:
            return None

        # Dynamic flush: drawdowns >= 7.0% from the recent peak
        flush_pct = (window_high - window_low) / window_high
        if flush_pct < 0.07:
            return None

        # Ensure market was recently washed out (RSI oversold within flush period)
        rsi = self._rsi(closes, 14)
        if rsi is None or rsi > 42.0:
            return None

        # Structural Reversal Confirmation:
        # First bar showing stabilization by breaking and closing above the prior bar's high
        prev_bar_high = highs[-2]
        if current_close > prev_bar_high and closes[-2] <= highs[-3]:
            # Calculate structural recovery anchors
            self.panic_low = window_low
            self.panic_high = window_high
            self.target_midpoint = (window_high + window_low) / 2.0

            # Dynamic risk sizing
            dist_to_low = max(current_close - self.panic_low, 0.001)
            stop_loss_bps = max(180.0, min(500.0, (dist_to_low / current_close) * 10000.0))

            dist_to_mid = max(self.target_midpoint - current_close, 0.001)
            take_profit_bps = max(250.0, min(650.0, (dist_to_mid / current_close) * 10000.0))

            # Confidence scales with flush depth
            confidence = min(0.90, max(0.60, 0.50 + flush_pct * 3.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_panic_flush_reversal_confirmed",
                    "flush_pct": flush_pct,
                    "rsi": rsi,
                    "panic_low": self.panic_low,
                    "target_midpoint": self.target_midpoint,
                    "prev_bar_high": prev_bar_high,
                    "close": current_close
                }
            )

        return None