from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 36
        self.rsi_period = 9
        self.min_width_bps = 75.0
        self.max_width_bps = 185.0
        self.cooldown_bars = 28
        self.last_trade_bar = -100

    def _rsi(self, closes, period=9):
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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)
        closes = ctx.closes(self.channel_period)

        if len(closes) < self.channel_period:
            return None

        channel_high = max(highs)
        channel_low = min(lows)
        midline = (channel_high + channel_low) / 2.0
        channel_range = channel_high - channel_low

        if midline <= 0 or channel_range <= 0:
            return None

        width_bps = (channel_range / midline) * 10000.0

        # Position Management: Exit cleanly at midline reversion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            current_close = ctx.bar.close

            if pos_dir == "long" and current_close >= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_hit_channel_midline",
                        "midline": round(midline, 3),
                        "close": current_close,
                        "width_bps": round(width_bps, 1),
                    },
                )
            elif pos_dir == "short" and current_close <= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_hit_channel_midline",
                        "midline": round(midline, 3),
                        "close": current_close,
                        "width_bps": round(width_bps, 1),
                    },
                )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter: Stand down during high volatility/crisis
        if ctx.regime in ("volatile", "crisis"):
            return None
        if ctx.market.get("regime", "NORMAL") in ("CRISIS", "MELTDOWN"):
            return None

        # Bandwidth check: Tape must be compressed (chop), not expanding into a trend
        if width_bps < self.min_width_bps or width_bps > self.max_width_bps:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        lower_threshold = channel_low + (channel_range * 0.15)
        upper_threshold = channel_high - (channel_range * 0.15)

        # Long Entry: Near channel low + oversold RSI + green rejection bar
        if current_close <= lower_threshold and rsi <= 32.0 and current_close > current_open:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.70,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_low_bounce",
                    "rsi": round(rsi, 2),
                    "width_bps": round(width_bps, 1),
                    "channel_low": round(channel_low, 3),
                    "midline": round(midline, 3),
                    "close": current_close,
                },
            )

        # Short Entry: Near channel high + overbought RSI + red rejection bar
        if current_close >= upper_threshold and rsi >= 68.0 and current_close < current_open:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.70,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_high_rejection",
                    "rsi": round(rsi, 2),
                    "width_bps": round(width_bps, 1),
                    "channel_high": round(channel_high, 3),
                    "midline": round(midline, 3),
                    "close": current_close,
                },
            )

        return None