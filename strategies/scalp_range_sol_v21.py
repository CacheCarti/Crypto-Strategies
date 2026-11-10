from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_period = 25
        self.last_exit_bar = -100
        self.min_range_pct = 0.006
        self.max_range_pct = 0.020

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 1)
        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)

        if len(closes) < self.channel_period + 1:
            return None

        # Exclude current bar from channel extremes to prevent lookahead / immediate breach
        prev_highs = highs[:-1]
        prev_lows = lows[:-1]
        c_high = max(prev_highs)
        c_low = min(prev_lows)
        midline = (c_high + c_low) / 2.0
        channel_width = c_high - c_low

        if midline <= 0:
            return None

        width_pct = channel_width / midline

        # Exit at midline if holding a position
        if ctx.has_position():
            direction = ctx.position_direction()
            current_close = ctx.bar.close
            if direction == "long" and current_close >= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_hit_midline",
                    "price": current_close,
                    "midline": midline
                })
            elif direction == "short" and current_close <= midline:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_hit_midline",
                    "price": current_close,
                    "midline": midline
                })
            return None

        # Enforce strict cooldown between trades to maintain low frequency (target ~30-70 trades / 17k bars)
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Market regime filter: stand down in volatile/crisis markets
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("HIGH_VOL", "CRISIS", "MELTDOWN"):
            return None

        # Filter out trending/widening channels or dead markets
        if width_pct < self.min_range_pct or width_pct > self.max_range_pct:
            return None

        # Short RSI calculation for confirming oversold/overbought edges
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        period_rsi = 7
        avg_gain = sum(gains[-period_rsi:]) / period_rsi
        avg_loss = sum(losses[-period_rsi:]) / period_rsi
        rsi = 50.0
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        elif avg_gain > 0:
            rsi = 100.0

        current_price = ctx.bar.close

        # Long: Pierced lower edge (within 5% of channel bottom) + RSI oversold + bounce off bar low
        if current_price <= c_low + 0.05 * channel_width and rsi < 32 and ctx.bar.close > ctx.bar.low:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=160.0,
                horizon_seconds=900,
                metadata={
                    "reason": "range_edge_fade_long",
                    "price": current_price,
                    "c_low": c_low,
                    "c_high": c_high,
                    "rsi": round(rsi, 2),
                    "width_pct": round(width_pct, 4)
                }
            )

        # Short: Pierced upper edge (within 5% of channel top) + RSI overbought + rejection off bar high
        if current_price >= c_high - 0.05 * channel_width and rsi > 68 and ctx.bar.close < ctx.bar.high:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=120.0,
                take_profit_bps=160.0,
                horizon_seconds=900,
                metadata={
                    "reason": "range_edge_fade_short",
                    "price": current_price,
                    "c_low": c_low,
                    "c_high": c_high,
                    "rsi": round(rsi, 2),
                    "width_pct": round(width_pct, 4)
                }
            )

        return None