from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold_pct = 0.07  # 7% drop over lookback
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.panic_low = 0.0
        self.panic_high = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        volumes = ctx.volumes(30)

        if len(closes) < 26:
            return None

        # Position management: manual midpoint target exit
        if ctx.has_position():
            target_price = 0.5 * (self.panic_low + self.panic_high)
            if ctx.bar.close >= target_price and target_price > 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reached",
                        "price": ctx.bar.close,
                        "panic_low": self.panic_low,
                        "panic_high": self.panic_high,
                        "midpoint": target_price
                    }
                )
            return None

        # Cooldown guard to avoid churn after flushed exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Event detection: 7%+ flush over last 24 bars
        recent_high = max(highs[-self.lookback-1:-1])
        recent_low = min(lows[-self.lookback-1:-1])
        drop = (recent_high - recent_low) / recent_high

        if drop < self.drop_threshold_pct:
            return None

        # Confirm panic exhaustion: current bar stabilizes by breaking previous bar's high
        prev_high = highs[-2]
        curr_close = closes[-1]
        is_breakout = curr_close > prev_high

        # Ensure we are recovering from near the bottom (in lower 35% of flush range)
        range_size = recent_high - recent_low
        dist_from_low = (curr_close - recent_low) / range_size if range_size > 0 else 1.0

        if is_breakout and dist_from_low < 0.40:
            # Volume confirmation: volume expanding relative to 10-bar baseline
            avg_vol = sum(volumes[-11:-1]) / 10.0
            vol_expansion = volumes[-1] >= avg_vol * 1.1

            if not vol_expansion:
                return None

            self.panic_low = recent_low
            self.panic_high = recent_high

            # Dynamic SL based on panic low distance (clamped between 200 and 500 bps)
            dist_to_panic_low_bps = ((curr_close - recent_low) / curr_close) * 10000.0
            sl_bps = max(200.0, min(500.0, dist_to_panic_low_bps + 50.0))
            
            # Target flush range midpoint
            midpoint = 0.5 * (recent_low + recent_high)
            tp_bps = max(300.0, ((midpoint - curr_close) / curr_close) * 10000.0)

            confidence = min(0.95, max(0.55, drop * 6.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=28800,
                metadata={
                    "reason": "sol_panic_reversal_stabilization",
                    "drop_pct": round(drop * 100, 2),
                    "dist_from_low": round(dist_from_low, 3),
                    "panic_low": recent_low,
                    "panic_high": recent_high,
                    "prev_high": prev_high,
                    "curr_close": curr_close,
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1)
                }
            )

        return None