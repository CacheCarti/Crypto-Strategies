from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcCapitulationRecovery(Strategy):
    METADATA = {
        "name": "BtcCapitulationRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # 6-hour baseline swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.entry_midpoint = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Midpoint take profit or time horizon exit
            if current_close >= self.entry_midpoint and self.entry_midpoint > 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target",
                        "close": current_close,
                        "midpoint": self.entry_midpoint,
                        "bars_held": bars_held,
                    }
                )
            if bars_held >= 12:  # max hold 12 hours
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_timeout",
                        "close": current_close,
                        "bars_held": bars_held,
                    }
                )
            return None

        # Mandatory cooldown after position exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Measure 24-bar rolling drawdown / flush
        recent_high = max(highs[-self.lookback - 1:-1])
        recent_low = min(lows[-self.lookback - 1:-1])
        if recent_high <= 0:
            return None

        rolling_drop_pct = (recent_high - current_close) / recent_high

        # Volatility-adaptive drop requirement: max(4.5%, 2.5 * ATR_pct)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None
        atr_pct = atr / current_close
        dynamic_drop_threshold = max(0.045, 2.5 * atr_pct)

        # Reversal trigger: close breaks above prior bar's high
        prev_bar_high = highs[-2]
        is_breakout_reversal = current_close > prev_bar_high

        # Market regime filter: avoid meltdown / extreme crisis where flushes cascade
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        if rolling_drop_pct >= dynamic_drop_threshold and is_breakout_reversal:
            self.entry_bar = ctx.bar_index
            self.entry_midpoint = (recent_high + recent_low) / 2.0

            # Scale confidence with drop magnitude up to 1.0
            confidence = min(0.9, max(0.6, 0.6 + (rolling_drop_pct - dynamic_drop_threshold) * 5.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_reversal_confirmation",
                    "drop_pct": round(rolling_drop_pct * 100, 2),
                    "dynamic_threshold_pct": round(dynamic_drop_threshold * 100, 2),
                    "recent_high": recent_high,
                    "prev_bar_high": prev_bar_high,
                    "close": current_close,
                    "midpoint_target": self.entry_midpoint,
                }
            )

        return None