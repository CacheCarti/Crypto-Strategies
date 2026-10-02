from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecoveryReversion(Strategy):
    METADATA = {
        "name": "BTC Panic Recovery Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.flush_drop_threshold = 0.024  # 2.4% flush over window
        self.cooldown_bars = 5
        self.max_hold_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = 0
        self.target_midpoint = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_bars + 2)
        highs = ctx.highs(self.lookback_bars + 2)
        lows = ctx.lows(self.lookback_bars + 2)

        if len(closes) < self.lookback_bars + 2:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        prev_high = highs[-2]
        fg_index = ctx.features.get("fear_greed_index", 50.0)

        # In-position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar

            # Midpoint mean-reversion target reached
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "target_midpoint": round(self.target_midpoint, 2),
                        "close": current_close,
                        "bars_held": bars_held,
                    },
                )

            # Max hold duration exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.60,
                    metadata={
                        "reason": "max_hold_timeout",
                        "bars_held": bars_held,
                        "close": current_close,
                    },
                )

            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Lookback metrics over the flush window
        window_highs = highs[-self.lookback_bars:]
        window_lows = lows[-self.lookback_bars:]
        window_high = max(window_highs)
        window_low = min(window_lows)

        if window_high <= 0:
            return None

        # Measure drop depth from peak to trough in window
        window_drop = (window_high - window_low) / window_high

        # Flush condition: drop >= threshold and recent low occurred recently
        is_flush = window_drop >= self.flush_drop_threshold

        # Capitulation reversal trigger:
        # Bullish candle that reclaims the previous bar's high or strong green close
        is_reclaim = (current_close > prev_high) and (current_close > current_open)

        # Ensure price is still below the midpoint to allow bounce room
        midpoint = (window_high + window_low) / 2.0
        has_room = current_close < midpoint

        if is_flush and is_reclaim and has_room:
            self.entry_bar = ctx.bar_index
            self.target_midpoint = midpoint

            # Confidence boosted in fearful market
            confidence = 0.70
            if fg_index < 35:
                confidence = 0.85
            elif fg_index < 50:
                confidence = 0.75

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "panic_flush_capitulation_reclaim",
                    "window_drop_pct": round(window_drop * 100, 2),
                    "window_high": round(window_high, 2),
                    "window_low": round(window_low, 2),
                    "target_midpoint": round(midpoint, 2),
                    "fear_greed_index": fg_index,
                    "close": current_close,
                    "prev_high": prev_high,
                },
            )

        return None