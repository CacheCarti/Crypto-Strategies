from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 6
        self.last_trade_bar = -999
        self.min_channel_bps = 35.0
        self.max_channel_bps = 140.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period + 1:
            return None

        # Exclude the very current bar when determining the reference channel boundary
        ref_highs = highs[-self.channel_period - 1:-1]
        ref_lows = lows[-self.channel_period - 1:-1]
        chan_high = max(ref_highs)
        chan_low = min(ref_lows)
        midline = (chan_high + chan_low) / 2.0

        current_close = ctx.bar.close
        current_low = ctx.bar.low
        current_high = ctx.bar.high

        channel_width_bps = ((chan_high - chan_low) / midline) * 10000.0 if midline > 0 else 0.0

        # Position exit management: take profit or flatten if price crosses the midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close >= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_midline_reached",
                        "close": current_close,
                        "midline": midline,
                    }
                )
            elif direction == "short" and current_close <= midline:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_midline_reached",
                        "close": current_close,
                        "midline": midline,
                    }
                )
            return None

        # Mandatory post-trade cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Market regime filter: stand down in volatile/crisis markets or when channel widens into a breakout
        if ctx.regime in ("volatile", "crisis"):
            return None

        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("HIGH_VOL", "CRISIS", "MELTDOWN"):
            return None

        # Channel width filter: ensure chop, not a raging breakout or dead tape
        if not (self.min_channel_bps <= channel_width_bps <= self.max_channel_bps):
            return None

        rsi = self._rsi(closes, period=10)
        if rsi is None:
            return None

        # Edge fade setups: sweep edge with an RSI momentum exhaustion condition
        edge_tolerance = 0.0010 * midline  # within 10 bps of edge

        # Long fade at channel bottom
        if current_low <= (chan_low + edge_tolerance) and current_close > current_low and rsi <= 35.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_low_sweep_fade",
                    "close": current_close,
                    "chan_low": chan_low,
                    "midline": midline,
                    "channel_bps": round(channel_width_bps, 1),
                    "rsi": round(rsi, 2),
                }
            )

        # Short fade at channel top
        if current_high >= (chan_high - edge_tolerance) and current_close < current_high and rsi >= 65.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_high_sweep_fade",
                    "close": current_close,
                    "chan_high": chan_high,
                    "midline": midline,
                    "channel_bps": round(channel_width_bps, 1),
                    "rsi": round(rsi, 2),
                }
            )

        return None