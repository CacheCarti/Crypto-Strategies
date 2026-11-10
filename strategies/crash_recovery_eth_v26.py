from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.05
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position Management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Exit on reaching the rebound midpoint of the panic drop
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "target_midpoint": round(self.target_midpoint, 2),
                        "close": round(current_close, 2),
                        "bars_held": bars_held,
                    }
                )

            # Max hold time safeguard (15 bars = 15 hours)
            if bars_held >= 15:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_stop_expired",
                        "close": round(current_close, 2),
                        "bars_held": bars_held,
                    }
                )
            return None

        # Cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Detect Flush Event
        # Measure 24-bar cumulative drop from highest high
        flush_window_high = max(highs[-self.lookback:])
        flush_window_low = min(lows[-self.lookback:])
        current_close = ctx.bar.close
        
        drawdown_24 = (current_close - flush_window_high) / flush_window_high
        
        # Check strict structural flush condition
        if drawdown_24 <= self.drop_thresh:
            # Stabilization trigger: current close breaks above previous bar's high
            prev_high = highs[-2]
            if current_close > prev_high:
                # Target midpoint of the structural flush
                midpoint = flush_window_low + (flush_window_high - flush_window_low) * 0.50
                
                # Ensure the midpoint offers adequate reward-to-risk (> 60 bps)
                expected_gain_bps = ((midpoint - current_close) / current_close) * 10000.0
                if expected_gain_bps < 60.0:
                    return None

                self.entry_bar = ctx.bar_index
                self.target_midpoint = midpoint

                # Scale confidence by panic magnitude (deeper flushes get higher conviction)
                confidence = min(0.9, 0.65 + abs(drawdown_24) * 2.0)

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "panic_flush_stabilization_long",
                        "drawdown_24_pct": round(drawdown_24 * 100.0, 2),
                        "flush_high": round(flush_window_high, 2),
                        "flush_low": round(flush_window_low, 2),
                        "target_midpoint": round(midpoint, 2),
                        "close": round(current_close, 2),
                        "prev_high": round(prev_high, 2),
                    }
                )

        return None