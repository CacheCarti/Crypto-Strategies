from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold max
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold = -0.050  # 5% drop over 24h window
        self.max_hold_bars = 10
        self.entry_bar_idx = -1
        self.cooldown_bars = 12
        self.last_exit_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar_idx = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx if self.entry_bar_idx != -1 else 0
            
            # Structural exit: Reached 24-bar midpoint (50% retracement of the flush)
            midpoint = (max(highs[-self.lookback:]) + min(lows[-self.lookback:])) / 2.0
            if ctx.bar.close >= midpoint:
                return ctx.signal(
                    "flat", 
                    confidence=0.7, 
                    metadata={"reason": "reached_flush_midpoint", "close": ctx.bar.close, "midpoint": midpoint}
                )

            # Timed volatility exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat", 
                    confidence=0.6, 
                    metadata={"reason": "max_bars_held_reached", "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 1. Detect 24-bar flush
        peak_24 = max(highs[-self.lookback:])
        current_close = ctx.bar.close
        cum_ret = (current_close - peak_24) / peak_24

        # Regime/Sentiment filter: Panic environment or high fear
        fgi = ctx.features.get("fear_greed_index", 50.0)

        # 2. Capitulation reversal confirmation:
        # Current bar closes above previous bar's high after a severe drop
        prev_high = highs[-2]
        is_breakout_reversal = (current_close > prev_high) and (closes[-2] <= highs[-3])

        if cum_ret <= self.flush_threshold and is_breakout_reversal and fgi <= 45.0:
            atr = self._atr(highs, lows, closes, 14)
            atr_val = atr if atr is not None else (current_close * 0.015)

            # Dynamic Volatility Stop: Set slightly beyond the recent 3-bar swing low
            local_low = min(lows[-3:])
            dist_to_low_bps = ((current_close - local_low) / current_close) * 10000.0
            sl_bps = max(180.0, min(dist_to_low_bps + 40.0, 400.0))

            # Profit target scaled by recent drop magnitude
            midpoint = (peak_24 + local_low) / 2.0
            tp_bps = max(250.0, min(((midpoint - current_close) / current_close) * 10000.0, 550.0))

            self.entry_bar_idx = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=36000,
                metadata={
                    "reason": "crash_recovery_capitulation_break",
                    "cum_24h_drop_pct": round(cum_ret * 100, 2),
                    "fgi": fgi,
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1),
                    "atr": round(atr_val, 2),
                }
            )

        return None