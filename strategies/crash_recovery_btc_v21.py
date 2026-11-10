from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecoveryEventStudy(Strategy):
    METADATA = {
        "name": "BtcPanicRecoveryEventStudy",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 43200,  # ~12 hours swing hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold_pct = -0.05  # -5% drop over 24 bars (~500 bps flush)
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.target_price = 0.0
        self.max_hold_bars = 18

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1
        self.target_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Midpoint target reached or time-based horizon exit
            if current_close >= self.target_price and self.target_price > 0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "target_midpoint_hit",
                        "target_price": self.target_price,
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )

            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "event_horizon_timeout",
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            return None

        # Check cooldown to prevent clustering
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # 24-bar window drop calculation
        past_24_high = max(highs[-self.lookback:])
        past_24_low = min(lows[-self.lookback:])
        peak_close = max(closes[-self.lookback:])
        ret_from_peak = (current_close - peak_close) / peak_close

        # Capitulation condition:
        # 1. 24-bar flush of at least -5%
        # 2. Reversal trigger: close breaks above prior bar's high
        prev_high = highs[-2]
        is_flush = ret_from_peak <= self.drop_threshold_pct
        is_capitulation_breakout = current_close > prev_high

        # Avoid entries in catastrophic meltdowns if crisis score is extreme
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if is_flush and is_capitulation_breakout and crisis_score < 0.90:
            midpoint = (past_24_high + past_24_low) / 2.0
            
            # Ensure there is sufficient recovery headroom to the midpoint (at least 150 bps)
            headroom_bps = ((midpoint - current_close) / current_close) * 10000.0
            if headroom_bps < 150.0:
                return None

            self.target_price = midpoint
            self.entry_bar = ctx.bar_index

            confidence = min(0.9, max(0.6, abs(ret_from_peak) * 10.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=400.0,
                take_profit_bps=600.0,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "capitulation_rebound_above_prev_high",
                    "ret_from_peak": ret_from_peak,
                    "target_midpoint": midpoint,
                    "prev_high": prev_high,
                    "close": current_close,
                    "crisis_score": crisis_score
                }
            )

        return None