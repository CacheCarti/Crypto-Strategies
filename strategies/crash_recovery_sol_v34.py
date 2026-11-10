from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.panic_low = 0.0
        self.target_midpoint = 0.0

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
        self.panic_low = 0.0
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Position management
        if ctx.has_position():
            # Check for midpoint target reach or trailing failure
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "target_midpoint_reached",
                        "target": self.target_midpoint,
                        "close": current_close,
                        "entry_bar": self.entry_bar,
                    }
                )

            # Emergency time stop (hold too long without recovery)
            if (ctx.bar_index - self.entry_bar) > 24:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_stop_expired",
                        "bars_held": ctx.bar_index - self.entry_bar,
                        "close": current_close,
                    }
                )
            return None

        # Post-exit cooldown gate
        if bars_since_exit < self.cooldown_bars:
            return None

        # Measure rolling window drop
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        peak_to_close_drop = (recent_high - current_close) / recent_high

        # Relative panic condition: drop >= 6.5% over the 24-bar window
        if peak_to_close_drop < 0.065:
            return None

        prev_high = highs[-2]
        prev_close = closes[-2]

        # Stabilization confirmation:
        # 1. Bar closes strictly above previous bar's high
        # 2. Bar is bullish (close > open)
        # 3. Bar closed off the lows
        is_stabilization = (current_close > prev_high) and (current_close > ctx.bar.open)

        if not is_stabilization:
            return None

        # Filter out meltdown states if crisis score is extreme
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Derive dynamic target & stop
        flush_low = recent_low
        midpoint = (recent_high + flush_low) / 2.0

        # Calculate SL in bps based on distance below flush low
        risk_dist = current_close - flush_low
        if risk_dist <= 0:
            sl_bps = 250.0
        else:
            sl_bps = min(500.0, max(150.0, (risk_dist / current_close) * 10000.0 + 30.0))

        # Calculate TP in bps targeting flush midpoint
        reward_dist = midpoint - current_close
        if reward_dist > 0:
            tp_bps = min(600.0, max(200.0, (reward_dist / current_close) * 10000.0))
        else:
            tp_bps = 350.0

        # Store internal state
        self.panic_low = flush_low
        self.target_midpoint = midpoint
        self.entry_bar = ctx.bar_index

        confidence = min(0.9, max(0.55, 0.5 + peak_to_close_drop * 2.0))

        return ctx.signal(
            "long",
            confidence=round(confidence, 2),
            stop_loss_bps=round(sl_bps, 1),
            take_profit_bps=round(tp_bps, 1),
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sol_flush_stabilization_above_prev_high",
                "drop_pct": round(peak_to_close_drop * 100, 2),
                "flush_high": round(recent_high, 2),
                "flush_low": round(flush_low, 2),
                "midpoint": round(midpoint, 2),
                "close": round(current_close, 2),
                "crisis_score": round(crisis_score, 2),
            }
        )