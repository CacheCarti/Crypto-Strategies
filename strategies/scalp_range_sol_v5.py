from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolChopFadeScalp(Strategy):
    METADATA = {
        "name": "SolChopFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 900,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 36
        self.rsi_period = 9
        self.min_width_bps = 60.0
        self.max_width_bps = 135.0
        self.fade_threshold_pct = 0.08
        self.rsi_oversold = 28.0
        self.rsi_overbought = 72.0
        self.cooldown_bars = 75
        self.last_exit_bar = -200

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
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.channel_period + 1)
        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)

        if len(closes) < self.channel_period + 1 or len(highs) < self.channel_period or len(lows) < self.channel_period:
            return None

        highest_high = max(highs)
        lowest_low = min(lows)
        span = highest_high - lowest_low
        midline = (highest_high + lowest_low) / 2.0

        if midline <= 0:
            return None

        width_bps = (span / midline) * 10000.0
        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        current_close = ctx.bar.close
        pos_dir = ctx.position_direction()

        # Position exit management: exit as soon as price reverts back to channel midline
        if pos_dir == "long":
            if current_close >= midline or rsi_val >= 55.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "long_fade_target_or_rsi_neutralized",
                        "price": current_close,
                        "midline": midline,
                        "rsi": round(rsi_val, 2),
                        "width_bps": round(width_bps, 2),
                    },
                )
            return None

        if pos_dir == "short":
            if current_close <= midline or rsi_val <= 45.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "short_fade_target_or_rsi_neutralized",
                        "price": current_close,
                        "midline": midline,
                        "rsi": round(rsi_val, 2),
                        "width_bps": round(width_bps, 2),
                    },
                )
            return None

        # Hard multi-bar cooldown after previous exit to keep trade count low and avoid friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime filters: only trade calm/neutral sideways tape, stand down in trending or volatile conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if ctx.regime in ["volatile", "crisis"] or crisis_score > 0.25 or trend_regime != "neutral":
            return None

        # Channel width filter: ensure range is narrow chop, not expanding into momentum breakout
        if width_bps < self.min_width_bps or width_bps > self.max_width_bps:
            return None

        lower_bound = lowest_low + span * self.fade_threshold_pct
        upper_bound = highest_high - span * self.fade_threshold_pct

        # Long Entry: Price touching lower bound of narrow channel with extreme oversold RSI
        if current_close <= lower_bound and rsi_val <= self.rsi_oversold:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.80,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "channel_bottom_extreme_fade",
                    "price": current_close,
                    "channel_low": lowest_low,
                    "channel_high": highest_high,
                    "midline": midline,
                    "width_bps": round(width_bps, 2),
                    "rsi": round(rsi_val, 2),
                },
            )

        # Short Entry: Price touching upper bound of narrow channel with extreme overbought RSI
        if current_close >= upper_bound and rsi_val >= self.rsi_overbought:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.80,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "channel_top_extreme_fade",
                    "price": current_close,
                    "channel_low": lowest_low,
                    "channel_high": highest_high,
                    "midline": midline,
                    "width_bps": round(width_bps, 2),
                    "rsi": round(rsi_val, 2),
                },
            )

        return None