from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicFlushRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 3600 * 18,  # ~18 hours recovery horizon
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.055  # 5.5% drop over 24h
        self.cooldown_bars = 12
        self.max_hold_bars = 20
        self.last_exit_bar = -999
        self.entry_bar_idx = -999
        self.target_midpoint = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_idx = ctx.bar_index
        current_close = ctx.bar.close

        # Position Management
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar_idx
            
            # Midpoint take-profit or timed exit to solve prior structural giveback stops
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "target_midpoint": self.target_midpoint,
                    "close": current_close,
                    "bars_held": bars_held
                })

            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "timed_recovery_exit",
                    "bars_held": bars_held,
                    "close": current_close
                })

            return None

        # Cooldown guard after any exit
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute 24-bar drop
        ref_price = closes[-self.lookback]
        roll_return = (current_close - ref_price) / ref_price

        # Check for sharp flush
        if roll_return <= self.flush_threshold_pct:
            # Reversal confirmation: Close back above previous bar's high (capitulation/exhaustion bar)
            prev_high = highs[-2]
            if current_close > prev_high:
                atr_val = self._atr(highs, lows, closes, period=14)
                if atr_val is None or atr_val <= 0:
                    return None

                recent_high = max(highs[-self.lookback:])
                recent_low = min(lows[-self.lookback:])
                self.target_midpoint = (recent_high + recent_low) / 2.0

                # Volatility-based stop distance: beneath recent capitulation low with ATR buffer
                stop_dist = max(current_close - recent_low + (0.5 * atr_val), current_close * 0.015)
                sl_bps = min(max((stop_dist / current_close) * 10000.0, 180.0), 450.0)

                # Dynamic TP based on 24-bar midpoint
                midpoint_dist = max(self.target_midpoint - current_close, current_close * 0.02)
                tp_bps = min(max((midpoint_dist / current_close) * 10000.0, 250.0), 700.0)

                # Scale confidence by depth of flush
                flush_depth = abs(roll_return)
                confidence = min(0.65 + (flush_depth - 0.055) * 4.0, 0.95)

                self.entry_bar_idx = current_idx
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=3600 * self.max_hold_bars,
                    metadata={
                        "reason": "capitulation_reversal_above_prev_high",
                        "roll_return_24": roll_return,
                        "recent_low": recent_low,
                        "target_midpoint": self.target_midpoint,
                        "sl_bps": sl_bps,
                        "tp_bps": tp_bps,
                        "atr": atr_val
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0