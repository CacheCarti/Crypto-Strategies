from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFade(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 75.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_period = 12
        self.last_trade_bar = -999
        self.min_range_pct = 0.006   # 0.6% minimum span to cover 12 bps friction
        self.max_range_pct = 0.016   # 1.6% maximum span (sideways/compression only)

    def _rsi(self, closes, period=10):
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period + 1:
            return None

        # Calculate Donchian Channel on historical window (excluding current bar)
        hist_highs = highs[-self.channel_period - 1:-1]
        hist_lows = lows[-self.channel_period - 1:-1]
        
        highest_high = max(hist_highs)
        lowest_low = min(hist_lows)
        channel_span = highest_high - lowest_low
        midline = (highest_high + lowest_low) / 2.0

        current_close = ctx.bar.close
        current_low = ctx.bar.low
        current_high = ctx.bar.high

        # Relative channel range width
        range_pct = channel_span / midline if midline > 0 else 0.0

        # Position exit logic: close when price crosses back to midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_high >= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_tp_midline_reached",
                    "midline": midline,
                    "close": current_close
                })
            elif pos_dir == "short" and current_low <= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_tp_midline_reached",
                    "midline": midline,
                    "close": current_close
                })
            return None

        # Mandatory post-trade or post-exit cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_period:
            return None

        # Volatility filter: do not trade if market is trending or expanding rapidly
        if not (self.min_range_pct <= range_pct <= self.max_range_pct):
            return None

        # Skip during high-stress crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        rsi = self._rsi(closes, period=10)
        if rsi is None:
            return None

        # Event: Touch channel boundary and show rejection
        # Long Setup: Bar pierced near lower channel boundary, closed back up with low RSI
        boundary_buffer = channel_span * 0.12
        if current_low <= (lowest_low + boundary_buffer) and current_close > current_low and rsi < 36:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_lower_edge",
                    "rsi": rsi,
                    "channel_span_pct": range_pct,
                    "channel_low": lowest_low,
                    "midline": midline,
                    "close": current_close
                }
            )

        # Short Setup: Bar pierced near upper channel boundary, closed back down with high RSI
        if current_high >= (highest_high - boundary_buffer) and current_close < current_high and rsi > 64:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_upper_edge",
                    "rsi": rsi,
                    "channel_span_pct": range_pct,
                    "channel_high": highest_high,
                    "midline": midline,
                    "close": current_close
                }
            )

        return None