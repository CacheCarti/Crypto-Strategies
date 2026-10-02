from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SOL Range Fade Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 150.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 36
        self.rsi_period = 14
        self.min_width_bps = 80.0
        self.max_width_bps = 175.0
        self.edge_fraction = 0.08
        self.cooldown_period = 65
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.channel_period + 10)
        highs = ctx.highs(self.channel_period + 10)
        lows = ctx.lows(self.channel_period + 10)

        if len(closes) < self.channel_period + 2:
            return None

        chan_highs = highs[-self.channel_period:]
        chan_lows = lows[-self.channel_period:]
        highest = max(chan_highs)
        lowest = min(chan_lows)
        channel_span = highest - lowest
        midline = (highest + lowest) / 2.0

        if midline <= 0:
            return None

        width_bps = (channel_span / midline) * 10000.0
        curr_price = ctx.bar.close
        rsi = self._rsi(closes, self.rsi_period)

        if rsi is None:
            return None

        # Position management: take profit on clean midline mean-reversion
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_price >= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_midline_reversion_target",
                        "price": curr_price,
                        "midline": round(midline, 4),
                        "width_bps": round(width_bps, 1),
                        "rsi": round(rsi, 2),
                    },
                )
            elif direction == "short" and curr_price <= midline:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_midline_reversion_target",
                        "price": curr_price,
                        "midline": round(midline, 4),
                        "width_bps": round(width_bps, 1),
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # Cooldown guard: enforce hard multi-bar delay after entry or exit
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_period or bars_since_entry < self.cooldown_period:
            return None

        # Market regime filter: only trade in strictly neutral, non-crisis consolidation
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL") or crisis_score > 0.25:
            return None
        if trend_regime != "neutral":
            return None

        # Stand down if channel is trending/expanding or completely dead
        if width_bps < self.min_width_bps or width_bps > self.max_width_bps:
            return None

        lower_bound = lowest + channel_span * self.edge_fraction
        upper_bound = highest - channel_span * self.edge_fraction

        # Long entry setup: price tests lower channel boundary, RSI deeply oversold, bullish bounce candle
        bullish_reversal = ctx.bar.close > ctx.bar.open and ctx.bar.close > lows[-2]
        if ctx.bar.low <= lower_bound and rsi <= 26.0 and bullish_reversal:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (26.0 - rsi) * 0.015)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_support_oversold_bounce",
                    "price": curr_price,
                    "chan_low": round(lowest, 4),
                    "chan_high": round(highest, 4),
                    "midline": round(midline, 4),
                    "width_bps": round(width_bps, 1),
                    "rsi": round(rsi, 2),
                },
            )

        # Short entry setup: price tests upper channel boundary, RSI deeply overbought, bearish rejection candle
        bearish_reversal = ctx.bar.close < ctx.bar.open and ctx.bar.close < highs[-2]
        if ctx.bar.high >= upper_bound and rsi >= 74.0 and bearish_reversal:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (rsi - 74.0) * 0.015)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "fade_channel_resistance_overbought_rejection",
                    "price": curr_price,
                    "chan_low": round(lowest, 4),
                    "chan_high": round(highest, 4),
                    "midline": round(midline, 4),
                    "width_bps": round(width_bps, 1),
                    "rsi": round(rsi, 2),
                },
            )

        return None