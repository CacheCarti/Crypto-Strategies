from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # 5 hours target swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = -0.045  # 4.5% drop over 24h to capture robust flushes
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0

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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high

        # Manage existing open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Midpoint target of the drop window
            midpoint = (self.flush_high + self.flush_low) / 2.0 if self.flush_high > self.flush_low else 0.0

            if midpoint > 0.0 and current_close >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_flush_midpoint", "midpoint": midpoint, "bars_held": bars_held}
                )
            if bars_held >= 8:  # 8 hours maximum hold
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "time_stop_exhaustion", "bars_held": bars_held}
                )
            return None

        # Check post-exit mandatory cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate 24-bar return
        ref_price = closes[- (self.lookback + 1)]
        ret_24 = (current_close - ref_price) / ref_price

        # Check flush condition
        if ret_24 <= self.drop_threshold:
            # Capitulation confirmation: current close breaks above previous bar high
            prev_high = highs[-2]
            if current_close > prev_high:
                # Supplementary RSI filter to avoid fading structural non-oversold trends
                rsi_val = self._rsi(closes, period=14)
                if rsi_val is not None and rsi_val < 42.0:
                    self.flush_high = max(highs[-self.lookback:])
                    self.flush_low = min(lows[-self.lookback:])
                    self.entry_bar = ctx.bar_index

                    confidence = min(0.9, max(0.5, 0.5 + abs(ret_24) * 3.0))

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "flush_capitulation_reversal",
                            "ret_24": round(ret_24, 4),
                            "rsi": round(rsi_val, 2),
                            "prev_high": prev_high,
                            "close": current_close
                        }
                    )

        return None