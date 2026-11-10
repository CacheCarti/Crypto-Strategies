from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFade(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFade",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.rsi_period = 7
        self.cooldown_bars = 16
        self.last_trade_bar = -100

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            diff = closes[-i] - closes[-i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
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

        # Exclude the current forming/closing bar from channel extremes to prevent lookahead pinning
        prev_highs = highs[-self.channel_period - 1:-1]
        prev_lows = lows[-self.channel_period - 1:-1]
        
        channel_high = max(prev_highs)
        channel_low = min(prev_lows)
        midline = (channel_high + channel_low) / 2.0
        range_bps = ((channel_high - channel_low) / midline) * 10000.0

        current_close = closes[-1]
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Dynamic midline reversion exit
        if has_pos:
            if pos_dir == "long" and current_close >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_midline_reached",
                    "close": current_close,
                    "midline": midline
                })
            elif pos_dir == "short" and current_close <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_midline_reached",
                    "close": current_close,
                    "midline": midline
                })
            return None

        # Cooldown guard to avoid churn and strictly limit trade count (< 100 in 17k bars)
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Hard compression filter: Channel must be between 70 bps (meaningful) and 180 bps (strictly non-trending)
        if not (70.0 <= range_bps <= 180.0):
            return None

        # Cross-asset calm filter: BTC must not be undergoing high-momentum shock
        btc_24h = abs(ctx.features.get("btc_return_pct", 0.0))
        if btc_24h > 4.5:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        pos_in_range = (current_close - channel_low) / (channel_high - channel_low) if channel_high > channel_low else 0.5

        # Fade lower boundary during oversold micro-exhaustion
        if pos_in_range <= 0.12 and rsi < 28.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_low_compression",
                    "range_bps": range_bps,
                    "rsi": rsi,
                    "pos_in_range": pos_in_range,
                    "midline": midline
                }
            )

        # Fade upper boundary during overbought micro-exhaustion
        if pos_in_range >= 0.88 and rsi > 72.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_high_compression",
                    "range_bps": range_bps,
                    "rsi": rsi,
                    "pos_in_range": pos_in_range,
                    "midline": midline
                }
            )

        return None