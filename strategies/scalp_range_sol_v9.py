from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 36
        self.rsi_period = 14
        self.min_width_bps = 75.0
        self.max_width_bps = 160.0
        self.cooldown_bars = 28
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes, period=14):
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
        closes = ctx.closes(self.channel_period + 15)
        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)
        opens = ctx.opens(self.channel_period)

        if len(closes) < self.channel_period + 5 or len(highs) < self.channel_period or len(lows) < self.channel_period:
            return None

        # Stand down in crisis or high volatility market regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        trend_regime = ctx.market.get("trend_regime", "neutral")

        if market_regime in ["CRISIS", "MELTDOWN", "HIGH_VOL"] or crisis_score > 0.35:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "regime_stress_exit", "regime": market_regime, "crisis_score": crisis_score}
                )
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        highest = max(highs)
        lowest = min(lows)
        channel_range = highest - lowest
        midline = (highest + lowest) / 2.0

        if midline <= 0 or channel_range <= 0:
            return None

        width_bps = (channel_range / midline) * 10000.0
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Position management: exit when mean reversion target (midline) is achieved
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                if current_close >= midline or rsi >= 55.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_midline_target_reached",
                            "close": current_close,
                            "midline": midline,
                            "rsi": round(rsi, 2),
                            "width_bps": round(width_bps, 1)
                        }
                    )
            elif pos_dir == "short":
                if current_close <= midline or rsi <= 45.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_midline_target_reached",
                            "close": current_close,
                            "midline": midline,
                            "rsi": round(rsi, 2),
                            "width_bps": round(width_bps, 1)
                        }
                    )
            return None

        # Hard cooldowns to strictly prevent overtrading and friction drag
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Only scalp sideways/neutral tapes — avoid fading strong directional trends
        if trend_regime != "neutral":
            return None

        # Channel width bounds: must be a defined chop range, neither expanding nor dead
        if not (self.min_width_bps <= width_bps <= self.max_width_bps):
            return None

        channel_pos = (current_close - lowest) / channel_range

        # Strict Long Setup: deep channel extreme + extreme oversold + reversal bar confirmation
        if channel_pos <= 0.08 and rsi <= 28.0 and current_close > current_open:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (0.08 - channel_pos) * 2.0 + (28.0 - rsi) * 0.01)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_bottom_confirmed_reversal_long",
                    "channel_pos": round(channel_pos, 3),
                    "rsi": round(rsi, 2),
                    "width_bps": round(width_bps, 1),
                    "close": current_close,
                    "midline": midline,
                    "trend_regime": trend_regime
                }
            )

        # Strict Short Setup: top channel extreme + extreme overbought + rejection bar confirmation
        if channel_pos >= 0.92 and rsi >= 72.0 and current_close < current_open:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.85, 0.60 + (channel_pos - 0.92) * 2.0 + (rsi - 72.0) * 0.01)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_top_confirmed_rejection_short",
                    "channel_pos": round(channel_pos, 3),
                    "rsi": round(rsi, 2),
                    "width_bps": round(width_bps, 1),
                    "close": current_close,
                    "midline": midline,
                    "trend_regime": trend_regime
                }
            )

        return None