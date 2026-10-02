from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 25,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 18
        self.drop_threshold = 0.038  # 3.8% flush drop threshold
        self.cooldown_bars = 4
        self.last_exit_bar = -100
        self.target_midpoint = 0.0
        self.panic_low = 0.0
        self.entry_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0
        self.panic_low = 0.0
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 15)
        highs = ctx.highs(self.lookback_bars + 15)
        lows = ctx.lows(self.lookback_bars + 15)

        if len(closes) < self.lookback_bars + 14:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_low = ctx.bar.low
        rsi_val = self._rsi(closes, period=14) or 50.0

        # Position management
        if ctx.has_position():
            # Exit if flush range midpoint is reached or RSI shows overbought exhaustion
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "price": current_close,
                        "target_midpoint": round(self.target_midpoint, 2),
                        "rsi": round(rsi_val, 2),
                    },
                )
            if rsi_val >= 65.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_rebound_exhaustion",
                        "price": current_close,
                        "rsi": round(rsi_val, 2),
                    },
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Analyze recent flush range
        recent_highs = highs[-self.lookback_bars :]
        recent_lows = lows[-self.lookback_bars :]
        flush_high = max(recent_highs)
        flush_low = min(recent_lows)

        if flush_high <= 0:
            return None

        drop_pct = (flush_high - flush_low) / flush_high
        prev_high = highs[-2]
        prev_close = closes[-2]

        # Loosened Entry Conditions:
        # 1. Drop magnitude >= 3.8% over the recent lookback
        # 2. Bullish stabilization bar: current close > prev_high OR (close > prev_close and close > open)
        # 3. Not currently overbought (RSI <= 55)
        # 4. Entry price still below midpoint of the flush range
        is_panic_drop = drop_pct >= self.drop_threshold
        is_stabilization = (current_close > prev_high) or (
            current_close > prev_close and current_close > current_open
        )
        midpoint = (flush_high + flush_low) / 2.0
        has_upside_to_midpoint = current_close < midpoint
        rsi_eligible = rsi_val <= 55.0

        if is_panic_drop and is_stabilization and has_upside_to_midpoint and rsi_eligible:
            self.panic_low = flush_low
            self.target_midpoint = midpoint
            self.entry_price = current_close

            # Calculate dynamic SL based on distance to panic low
            distance_to_low_bps = ((current_close - self.panic_low) / current_close) * 10000.0
            sl_distance_bps = max(180.0, min(400.0, distance_to_low_bps + 35.0))
            
            distance_to_mid_bps = ((self.target_midpoint - current_close) / current_close) * 10000.0
            tp_distance_bps = max(220.0, min(500.0, distance_to_mid_bps))

            confidence = min(0.90, max(0.60, 0.60 + (drop_pct - self.drop_threshold) * 3.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_distance_bps,
                take_profit_bps=tp_distance_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "sol_panic_flush_stabilization_rebound",
                    "drop_pct": round(drop_pct * 100, 2),
                    "flush_high": flush_high,
                    "panic_low": self.panic_low,
                    "target_midpoint": round(self.target_midpoint, 2),
                    "current_close": current_close,
                    "rsi": round(rsi_val, 2),
                },
            )

        return None