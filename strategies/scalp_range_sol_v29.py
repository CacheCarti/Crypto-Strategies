from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.rsi_period = 8
        self.cooldown_bars = 16
        self.max_channel_width_pct = 1.35
        self.min_channel_width_pct = 0.45
        self.max_hold_bars = 7
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _rsi(self, closes, period):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 5)
        highs = ctx.highs(self.channel_period + 5)
        lows = ctx.lows(self.channel_period + 5)

        if len(closes) < self.channel_period + 2:
            return None

        # Exclude current forming bar from channel extremes to prevent lookahead bias
        hist_highs = highs[-(self.channel_period + 1):-1]
        hist_lows = lows[-(self.channel_period + 1):-1]
        chan_high = max(hist_highs)
        chan_low = min(hist_lows)
        midline = (chan_high + chan_low) / 2.0
        channel_span = chan_high - chan_low

        if midline <= 0:
            return None

        width_pct = (channel_span / midline) * 100.0
        current_close = ctx.bar.close

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Midline reversion target
            if direction == "long" and current_close >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "long_midline_hit", "midline": midline, "price": current_close})
            if direction == "short" and current_close <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "short_midline_hit", "midline": midline, "price": current_close})

            # Time stop to free capital in stagnant chop
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "time_stop_scalp", "bars_held": bars_held, "price": current_close})

            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: reject active crisis/meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility bandwidth filter: trade strictly during tight, rangebound chop
        if not (self.min_channel_width_pct <= width_pct <= self.max_channel_width_pct):
            return None

        # Fast momentum oscillator to confirm exhaustion at edges
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Long: price piercing or near channel floor with oversold exhaustion
        if current_close <= chan_low + 0.12 * channel_span and rsi < 32.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_low",
                    "width_pct": round(width_pct, 2),
                    "rsi": round(rsi, 1),
                    "chan_low": round(chan_low, 2),
                    "midline": round(midline, 2)
                }
            )

        # Short: price piercing or near channel ceiling with overbought exhaustion
        if current_close >= chan_high - 0.12 * channel_span and rsi > 68.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_high",
                    "width_pct": round(width_pct, 2),
                    "rsi": round(rsi, 1),
                    "chan_high": round(chan_high, 2),
                    "midline": round(midline, 2)
                }
            )

        return None