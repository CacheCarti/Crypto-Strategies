from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "SolPanicFlushRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # ~8h average hold on 1h bars
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.075  # 7.5% flush over 24h
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.active_flush_low = None
        self.active_midpoint = None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.active_flush_low = None
        self.active_midpoint = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            curr_close = ctx.bar.close
            # Take profit near the flush-range midpoint
            if self.active_midpoint and curr_close >= self.active_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "target_midpoint_reached", "close": curr_close, "midpoint": self.active_midpoint}
                )
            # Stop loss below the recorded panic swing low
            if self.active_flush_low and curr_close < self.active_flush_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "panic_low_violated", "close": curr_close, "panic_low": self.active_flush_low}
                )
            return None

        # Cooldown guard after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Event boundary: 24-bar rolling high to recent low flush
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        rolling_peak = max(window_highs)
        rolling_trough = min(window_lows)

        if rolling_peak <= 0:
            return None

        drop_pct = (rolling_peak - rolling_trough) / rolling_peak
        if drop_pct < self.min_drop_pct:
            return None

        # Stabilization confirmation: current close breaks above previous bar high
        prev_high = highs[-2]
        curr_close = closes[-1]
        curr_low = lows[-1]

        # Trigger on the first structural breakout bar following the dump
        if curr_close > prev_high and closes[-2] <= highs[-3]:
            # Calculate distance to panic low for dynamic stop, and midpoint for exit
            panic_low = rolling_trough
            midpoint = (rolling_peak + panic_low) / 2.0

            # Ensure reasonable upside potential to midpoint
            if midpoint <= curr_close:
                return None

            sl_dist_bps = ((curr_close - panic_low) / curr_close) * 10000.0
            tp_dist_bps = ((midpoint - curr_close) / curr_close) * 10000.0

            sl_bps = max(250.0, min(sl_dist_bps + 40.0, 500.0))
            tp_bps = max(350.0, min(tp_dist_bps, 700.0))

            # Store bounds for trade management
            self.active_flush_low = panic_low
            self.active_midpoint = midpoint

            # Confidence scaled by depth of flush
            conf = min(0.9, 0.55 + (drop_pct - self.min_drop_pct) * 3.0)

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=28800,
                metadata={
                    "reason": "sol_panic_flush_reversal",
                    "drop_pct": round(drop_pct * 100, 2),
                    "panic_low": panic_low,
                    "midpoint": midpoint,
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1)
                }
            )

        return None