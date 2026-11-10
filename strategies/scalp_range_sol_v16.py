from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolNarrowRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolNarrowRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 12
        self.last_trade_bar = -999
        self.max_channel_width_pct = 1.35
        self.min_channel_width_pct = 0.45
        self.edge_buffer = 0.12

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
        closes = ctx.closes(self.channel_period + 10)
        highs = ctx.highs(self.channel_period + 10)
        lows = ctx.lows(self.channel_period + 10)

        if len(closes) < self.channel_period + 5:
            return None

        current_close = ctx.bar.close
        recent_highs = highs[-self.channel_period:]
        recent_lows = lows[-self.channel_period:]
        
        ch_high = max(recent_highs)
        ch_low = min(recent_lows)
        ch_range = ch_high - ch_low
        midline = (ch_high + ch_low) / 2.0

        if current_close <= 0.0 or ch_range <= 0.0:
            return None

        channel_width_pct = (ch_range / current_close) * 100.0

        # Check existing position for exit logic
        if ctx.has_position():
            direction = ctx.position_direction()
            pos_dict = ctx.position or {}
            entry_price = pos_dict.get("entry_price", current_close)
            
            # Midline target or structural invalidation
            if direction == "long" and current_close >= midline:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "long_hit_midline",
                    "entry_price": entry_price,
                    "close": current_close,
                    "midline": midline
                })
            elif direction == "short" and current_close <= midline:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "short_hit_midline",
                    "entry_price": entry_price,
                    "close": current_close,
                    "midline": midline
                })

            # Trend expansion emergency flatten
            if channel_width_pct > self.max_channel_width_pct * 1.25:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "channel_expanded_trend_flatten",
                    "width_pct": channel_width_pct
                })
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Skip if market regime is in crisis/high vol
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        # Channel width condition: only fade narrow chop
        if not (self.min_channel_width_pct <= channel_width_pct <= self.max_channel_width_pct):
            return None

        rsi = self._rsi(closes, period=9)
        if rsi is None:
            return None

        long_boundary = ch_low + (ch_range * self.edge_buffer)
        short_boundary = ch_high - (ch_range * self.edge_buffer)

        # Long boundary touch + RSI oversold recovery setup
        if ctx.bar.low <= long_boundary and current_close > ctx.bar.open and rsi < 38.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.72,
                stop_loss_bps=85.0,
                take_profit_bps=115.0,
                horizon_seconds=600,
                metadata={
                    "reason": "fade_channel_low_reversal",
                    "ch_low": ch_low,
                    "ch_high": ch_high,
                    "ch_width_pct": channel_width_pct,
                    "rsi": rsi,
                    "price": current_close
                }
            )

        # Short boundary touch + RSI overbought rejection setup
        if ctx.bar.high >= short_boundary and current_close < ctx.bar.open and rsi > 62.0:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.72,
                stop_loss_bps=85.0,
                take_profit_bps=115.0,
                horizon_seconds=600,
                metadata={
                    "reason": "fade_channel_high_rejection",
                    "ch_low": ch_low,
                    "ch_high": ch_high,
                    "ch_width_pct": channel_width_pct,
                    "rsi": rsi,
                    "price": current_close
                }
            )

        return None