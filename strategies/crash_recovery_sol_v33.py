from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicStabilization(Strategy):
    METADATA = {
        "name": "SolPanicStabilization",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # ~8 hours swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.panic_low = 0.0
        self.target_midpoint = 0.0
        self.entry_bar = -100

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.panic_low = 0.0
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Manage open position
        if ctx.has_position():
            curr_c = ctx.bar.close
            # Dynamic exit: reaching midpoint target or breaking panic swing low
            if self.target_midpoint > 0 and curr_c >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "target": self.target_midpoint,
                    "close": curr_c
                })
            if self.panic_low > 0 and curr_c < self.panic_low:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "panic_low_violated_hard_exit",
                    "panic_low": self.panic_low,
                    "close": curr_c
                })
            # Time-based defensive exit after 20 bars
            if ctx.bar_index - self.entry_bar > 20:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_held_stale_trade",
                    "bars_held": ctx.bar_index - self.entry_bar
                })
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market filter: Do not catch knives during outright structural crisis
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Panic flush calculation: Drop >= 7.0% from highest high in lookback
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        curr_close = closes[-1]
        prev_high = highs[-2]
        prev_close = closes[-2]

        if recent_high <= 0:
            return None

        flush_depth_pct = (recent_high - recent_low) / recent_high

        # Event trigger: 7%+ drawdown in flush window, stabilization bar closing above previous bar's high
        if flush_depth_pct >= 0.07:
            # Stabilization confirmed: Current bar closes green and decisively clears previous bar's high
            if curr_close > prev_high and curr_close > prev_close:
                # Dynamic stop-loss and take-profit derived from event geometry
                stop_dist = max(curr_close - recent_low, curr_close * 0.02)
                sl_bps = min(max((stop_dist / curr_close) * 10000.0, 200.0), 500.0)

                # Target is near the flush range midpoint
                midpoint = (recent_high + recent_low) / 2.0
                if midpoint > curr_close:
                    tp_bps = min(max(((midpoint - curr_close) / curr_close) * 10000.0, 250.0), 650.0)
                else:
                    tp_bps = sl_bps * 1.5

                self.panic_low = recent_low
                self.target_midpoint = midpoint
                self.entry_bar = ctx.bar_index

                # Scaled confidence based on depth of washout
                conf = min(0.60 + (flush_depth_pct - 0.07) * 2.0, 0.95)

                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=28800,
                    metadata={
                        "reason": "panic_flush_stabilization_reversal",
                        "flush_depth_pct": round(flush_depth_pct, 4),
                        "recent_high": recent_high,
                        "recent_low": recent_low,
                        "midpoint": midpoint,
                        "price": curr_close,
                        "sl_bps": round(sl_bps, 1),
                        "tp_bps": round(tp_bps, 1)
                    }
                )

        return None