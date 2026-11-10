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
        self.rsi_period = 7
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.max_width_pct = 0.018  # Channel width must be < 1.8% to confirm sideways tape
        self.min_width_pct = 0.005  # Avoid zero-volatility dead tape

    def _rsi(self, closes, period=7):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(self.channel_period + 2)
        highs = ctx.highs(self.channel_period + 2)
        lows = ctx.lows(self.channel_period + 2)

        if len(closes) < self.channel_period + 1:
            return None

        # Filter out extreme crisis / runaway volatility regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "crisis_score": crisis_score}
                )
            return None

        # Calculate Donchian Channel over past N bars excluding the current bar
        recent_highs = highs[-self.channel_period - 1:-1]
        recent_lows = lows[-self.channel_period - 1:-1]
        c_high = max(recent_highs)
        c_low = min(recent_lows)
        midline = (c_high + c_low) / 2.0

        if midline <= 0:
            return None

        channel_width = c_high - c_low
        width_pct = channel_width / midline
        current_close = ctx.bar.close

        # Check existing position management: Exit at channel midline
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_close >= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "take_profit_midline_reached",
                        "close": current_close,
                        "midline": midline
                    }
                )
            elif pos_dir == "short" and current_close <= midline:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "take_profit_midline_reached",
                        "close": current_close,
                        "midline": midline
                    }
                )
            return None

        # Mandatory Cooldown check to prevent overtrading and fee drain
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Stand down if channel is expanding (breakout / trend starting) or dead flat
        if width_pct > self.max_width_pct or width_pct < self.min_width_pct:
            return None

        # Fast RSI to confirm micro-exhaustion at channel extremes
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Channel bounds definitions: extreme lower and upper 15% bands
        lower_threshold = c_low + 0.15 * channel_width
        upper_threshold = c_high - 0.15 * channel_width

        # LONG: Price probing lower band + oversold RSI reversal
        if current_close <= lower_threshold and rsi < 32.0:
            conf = min(0.85, 0.55 + (32.0 - rsi) * 0.01)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_support_oversold",
                    "close": current_close,
                    "channel_low": c_low,
                    "channel_high": c_high,
                    "width_pct": round(width_pct, 4),
                    "rsi": round(rsi, 2)
                }
            )

        # SHORT: Price probing upper band + overbought RSI reversal
        if current_close >= upper_threshold and rsi > 68.0:
            conf = min(0.85, 0.55 + (rsi - 68.0) * 0.01)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "range_fade_resistance_overbought",
                    "close": current_close,
                    "channel_low": c_low,
                    "channel_high": c_high,
                    "width_pct": round(width_pct, 4),
                    "rsi": round(rsi, 2)
                }
            )

        return None