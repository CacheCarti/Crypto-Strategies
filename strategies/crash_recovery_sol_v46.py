from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicReversal(Strategy):
    METADATA = {
        "name": "SolPanicReversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_price = 0.0
        self.target_midpoint = 0.0
        self.panic_low = 0.0
        self.max_hold_bars = 16

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_close = ctx.bar.close

            # Dynamic Target: Flush range midpoint or max hold reached
            if curr_close >= self.target_midpoint and self.target_midpoint > 0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_hit",
                        "target_midpoint": round(self.target_midpoint, 2),
                        "close": round(curr_close, 2),
                        "bars_held": bars_held
                    }
                )

            # Dynamic Structural Stop: Close below the panic flush low
            if curr_close < self.panic_low and self.panic_low > 0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "panic_low_violated",
                        "panic_low": round(self.panic_low, 2),
                        "close": round(curr_close, 2),
                        "bars_held": bars_held
                    }
                )

            # Time stop to free capital and prevent prolonged dead capital
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_stop_expired",
                        "close": round(curr_close, 2),
                        "bars_held": bars_held
                    }
                )

            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush Detection: Drop >= 6.5% over the lookback window
        # (Calibrated to achieve optimal ~30-60 selective trades in 1h SOL history)
        max_high = max(highs[-(self.lookback + 1):-1])
        min_low = min(lows[-(self.lookback + 1):-1])
        prior_close = closes[-2]
        curr_close = closes[-1]
        prev_high = highs[-2]

        drawdown = (max_high - min_low) / max_high
        is_flushed = drawdown >= 0.065

        # Stabilization Trigger: Current bar closes above prior bar's high, confirming buying pressure
        reversal_breakout = curr_close > prev_high

        # Avoid buying if regime is in catastrophic meltdown
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if is_flushed and reversal_breakout and regime != "MELTDOWN" and crisis_score < 0.85:
            self.entry_bar = ctx.bar_index
            self.entry_price = curr_close
            self.panic_low = min_low
            self.target_midpoint = (max_high + min_low) / 2.0

            # Scale stop loss dynamically to stay right below panic low
            risk_pct = (curr_close - min_low) / curr_close
            sl_bps = max(200.0, min(risk_pct * 10000.0 * 1.05, 500.0))
            tp_bps = max(250.0, min(((self.target_midpoint - curr_close) / curr_close) * 10000.0, 700.0))

            # Confidence based on flush depth and bounce power
            confidence = min(0.95, 0.65 + (drawdown - 0.065) * 2.5)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "sol_panic_flush_reversal",
                    "drawdown_pct": round(drawdown * 100, 2),
                    "flush_high": round(max_high, 2),
                    "flush_low": round(min_low, 2),
                    "target_midpoint": round(self.target_midpoint, 2),
                    "crisis_score": round(crisis_score, 2)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index