from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicRecoveryBtc(Strategy):
    METADATA = {
        "name": "PanicRecoveryBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours target recovery swing
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_pct_threshold = 0.055  # 5.5% drop over 24h
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.target_midpoint = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Manage existing position
        if ctx.has_position():
            curr_close = closes[-1]
            bars_held = ctx.bar_index - self.entry_bar

            # Take-profit at the flush midpoint or maximum time limit
            if (self.target_midpoint > 0.0 and curr_close >= self.target_midpoint) or bars_held >= 12:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_or_time_exit",
                        "bars_held": bars_held,
                        "target_midpoint": round(self.target_midpoint, 2),
                        "close": curr_close,
                    }
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Identify panic flush over the past 24 bars
        recent_high = max(highs[-self.lookback - 1 : -1])
        recent_low = min(lows[-self.lookback - 1 : -1])
        
        if recent_high <= 0:
            return None

        drawdown = (recent_high - recent_low) / recent_high
        
        # Check if flush threshold is satisfied
        if drawdown < self.drop_pct_threshold:
            return None

        # Capitulation reversal trigger: current bar closes above previous bar's high
        prev_high = highs[-2]
        curr_close = closes[-1]
        
        if curr_close > prev_high:
            # Reversal confirmation: price must be in the lower half of the 24-bar crash range
            midpoint = (recent_high + recent_low) / 2.0
            if curr_close >= midpoint:
                return None

            # Regime filter: avoid entries during deep chronic meltdown
            market_regime = ctx.market.get("regime", "NORMAL")
            if market_regime == "MELTDOWN":
                return None

            self.entry_bar = ctx.bar_index
            self.target_midpoint = midpoint
            confidence = min(0.9, 0.60 + (drawdown - self.drop_pct_threshold) * 3.0)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "capitulation_candle_reversal",
                    "drawdown_pct": round(drawdown * 100, 2),
                    "recent_high": round(recent_high, 2),
                    "recent_low": round(recent_low, 2),
                    "midpoint": round(midpoint, 2),
                    "close": curr_close,
                    "prev_high": prev_high,
                }
            )

        return None