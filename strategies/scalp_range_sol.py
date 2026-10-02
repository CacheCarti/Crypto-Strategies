from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolRangeFadeScalp(Strategy):
    METADATA = {
        "name": "SolRangeFadeScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.channel_period = 48
        self.rsi_period = 9
        self.min_range_pct = 0.010    # 100 bps min channel width to ensure profit beats friction
        self.max_range_pct = 0.026    # 260 bps max width (filter out expanding breakout ranges)
        self.cooldown_bars = 40       # Strict multi-hour cooldown between trades
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes, period=9) -> Optional[float]:
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
        closes = ctx.closes(self.channel_period + self.rsi_period + 5)
        highs = ctx.highs(self.channel_period)
        lows = ctx.lows(self.channel_period)

        if len(closes) < self.channel_period + 1 or len(highs) < self.channel_period:
            return None

        # Stand down in volatile or crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_risk_exit", "regime": regime})
            return None

        recent_highs = highs[-self.channel_period:]
        recent_lows = lows[-self.channel_period:]
        chan_high = max(recent_highs)
        chan_low = min(recent_lows)

        if chan_low <= 0:
            return None

        chan_range = chan_high - chan_low
        range_pct = chan_range / chan_low
        midline = (chan_high + chan_low) / 2.0
        current_close = ctx.bar.close
        current_open = ctx.bar.open

        # Position management: fade strictly to midline target
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "long_target_midline_hit",
                        "close": current_close,
                        "midline": midline,
                        "chan_high": chan_high,
                        "chan_low": chan_low,
                    },
                )
            elif direction == "short" and current_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "short_target_midline_hit",
                        "close": current_close,
                        "midline": midline,
                        "chan_high": chan_high,
                        "chan_low": chan_low,
                    },
                )
            return None

        # Hard cooldown check against both last exit and last entry
        if (ctx.bar_index - self.last_exit_bar < self.cooldown_bars) or (
            ctx.bar_index - self.last_entry_bar < self.cooldown_bars
        ):
            return None

        # Market trend filter: only scalp range fades during neutral or low-confidence trends
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime not in ("neutral", ""):
            return None

        # Channel width filter: ensure range is neither razor-thin nor expanding into trend
        if range_pct < self.min_range_pct or range_pct > self.max_range_pct:
            return None

        # Normalized position within channel (0.0 = low, 1.0 = high)
        chan_pos = (current_close - chan_low) / (chan_range + 1e-8)
        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        # High conviction Long: extreme channel bottom, deeply oversold RSI, and a green confirmation bounce
        if chan_pos <= 0.05 and rsi_val <= 26.0 and current_close > current_open:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.92, 0.65 + (0.05 - chan_pos) * 3.0 + (26.0 - rsi_val) * 0.01)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=100.0,
                take_profit_bps=140.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "range_bottom_rejection_long",
                    "chan_pos": round(chan_pos, 4),
                    "range_pct": round(range_pct, 4),
                    "rsi": round(rsi_val, 2),
                    "chan_low": chan_low,
                    "chan_high": chan_high,
                    "midline": round(midline, 4),
                },
            )

        # High conviction Short: extreme channel top, deeply overbought RSI, and a red confirmation rejection
        if chan_pos >= 0.95 and rsi_val >= 74.0 and current_close < current_open:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.92, 0.65 + (chan_pos - 0.95) * 3.0 + (rsi_val - 74.0) * 0.01)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=100.0,
                take_profit_bps=140.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "range_top_rejection_short",
                    "chan_pos": round(chan_pos, 4),
                    "range_pct": round(range_pct, 4),
                    "rsi": round(rsi_val, 2),
                    "chan_low": chan_low,
                    "chan_high": chan_high,
                    "midline": round(midline, 4),
                },
            )

        return None