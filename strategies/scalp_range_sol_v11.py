from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 90.0,
        "declared_tp_bps": 120.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.min_channel_width_pct = 0.5
        self.max_channel_width_pct = 1.8
        self.edge_threshold_pct = 0.12

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 1)
        highs = ctx.highs(self.channel_period + 1)
        lows = ctx.lows(self.channel_period + 1)

        if len(closes) < self.channel_period:
            return None

        # Market regime filter: avoid fading strong trending regimes or crises
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit"})
            return None

        current_close = closes[-1]
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Donchian channel calculations excluding current bar for true bounds
        recent_highs = highs[-self.channel_period - 1:-1]
        recent_lows = lows[-self.channel_period - 1:-1]
        highest = max(recent_highs)
        lowest = min(recent_lows)
        midline = (highest + lowest) / 2.0
        channel_range = highest - lowest

        if midline <= 0:
            return None

        channel_width_pct = (channel_range / midline) * 100.0

        # Position management: fade to midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_tp_midline_reached",
                        "price": current_close,
                        "midline": midline,
                        "channel_width_pct": channel_width_pct
                    }
                )
            elif pos_dir == "short" and current_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_tp_midline_reached",
                        "price": current_close,
                        "midline": midline,
                        "channel_width_pct": channel_width_pct
                    }
                )
            return None

        # Stand down during post-trade cooldown
        if bars_since_exit < self.cooldown_bars:
            return None

        # Check channel width: only fade when chop is established, abort if breakout trend expansion
        if channel_width_pct < self.min_channel_width_pct or channel_width_pct > self.max_channel_width_pct:
            return None

        # Normalized position in channel [0.0 = low, 1.0 = high]
        pos_in_range = (current_close - lowest) / channel_range if channel_range > 0 else 0.5

        # 7-period fast RSI to confirm local inflection
        rsi_period = 7
        if len(closes) < rsi_period + 1:
            return None
        gains, losses = [], []
        for i in range(-rsi_period, 0):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_g = sum(gains) / rsi_period
        avg_l = sum(losses) / rsi_period
        rsi = 100.0 if avg_l == 0 else 100.0 - (100.0 / (1.0 + (avg_g / avg_l)))

        # Long entry: price is near lower channel edge, RSI confirms oversold, not in a macro bear trend
        if pos_in_range <= self.edge_threshold_pct and rsi <= 35.0 and trend_regime != "bear":
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_support_rebound",
                    "pos_in_range": round(pos_in_range, 3),
                    "rsi": round(rsi, 2),
                    "channel_width_pct": round(channel_width_pct, 2),
                    "price": current_close,
                    "channel_low": lowest
                }
            )

        # Short entry: price is near upper channel edge, RSI confirms overbought, not in a macro bull trend
        if pos_in_range >= (1.0 - self.edge_threshold_pct) and rsi >= 65.0 and trend_regime != "bull":
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_resistance_reversal",
                    "pos_in_range": round(pos_in_range, 3),
                    "rsi": round(rsi, 2),
                    "channel_width_pct": round(channel_width_pct, 2),
                    "price": current_close,
                    "channel_high": highest
                }
            )

        return None