from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 350.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = -0.05
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0
        self.bars_in_position = 0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_position = 0
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_position += 1
            current_close = ctx.bar.close

            # Midpoint target exit or time-based stop
            if self.target_price > 0.0 and current_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "target_price": round(self.target_price, 2),
                        "close": round(current_close, 2),
                        "bars_held": self.bars_in_position,
                    }
                )
            
            # Max hold ceiling: 18 hours (18 bars on 1h)
            if self.bars_in_position >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_timeout",
                        "close": round(current_close, 2),
                        "bars_held": self.bars_in_position,
                    }
                )

            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime safety filter: avoid systemic crypto meltdown
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Calculate rolling 24-bar drop
        current_close = ctx.bar.close
        ref_close = closes[-self.lookback]
        cum_return = (current_close - ref_close) / ref_close

        # Entry condition:
        # 1. Past 24-bar cumulative drop exceeded -5%
        # 2. Structural stabilization: current close breaks strictly above previous bar's high
        # 3. Bar closed green (close > open) showing immediate buyer absorption
        if cum_return <= self.drop_threshold:
            prev_high = highs[-2]
            current_open = ctx.bar.open

            if current_close > prev_high and current_close > current_open:
                window_high = max(highs[-self.lookback:])
                window_low = min(lows[-self.lookback:])
                flush_range = window_high - window_low

                # Avoid degenerate ranges
                if flush_range <= 0:
                    return None

                midpoint = window_low + (flush_range * 0.5)

                # Ensure sufficient upside remains to the midpoint (> 100 bps)
                edge_bps = ((midpoint - current_close) / current_close) * 10000.0
                if edge_bps < 100.0:
                    return None

                self.flush_high = window_high
                self.flush_low = window_low
                self.target_price = midpoint
                self.bars_in_position = 0

                # Adaptive confidence scaled by severity of the capitulation
                severity = min(abs(cum_return) / 0.10, 1.0)
                confidence = max(0.65, min(0.95, 0.60 + 0.35 * severity))

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "panic_drop_stabilization_reversal",
                        "cum_return_24": round(cum_return, 4),
                        "window_low": round(window_low, 2),
                        "window_high": round(window_high, 2),
                        "target_midpoint": round(midpoint, 2),
                        "edge_bps": round(edge_bps, 1),
                        "current_close": round(current_close, 2),
                    }
                )

        return None