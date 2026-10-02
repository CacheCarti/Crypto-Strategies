from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityCoilRelease(Strategy):
    METADATA = {
        "name": "Volatility Coil Release",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 170,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.baseline_period = 144
        self.fast_atr_period = 6
        self.channel_period = 14
        self.spike_multiplier = 1.95
        self.compression_threshold = 1.10
        self.max_coil_age = 18
        self.cooldown_bars = 6

        self.spike_bar = -999
        self.coil_armed = False
        self.coil_armed_bar = -999
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_armed = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.baseline_period + 10
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        fast_atr = self._atr(highs, lows, closes, self.fast_atr_period)
        base_atr = self._atr(highs, lows, closes, self.baseline_period)

        if fast_atr is None or base_atr is None or base_atr == 0:
            return None

        current_bar = ctx.bar_index
        atr_ratio = fast_atr / base_atr

        # 1. Detect volatility spike
        if atr_ratio >= self.spike_multiplier:
            self.spike_bar = current_bar
            self.coil_armed = False

        # 2. Transition from spike to compression (coil armed)
        if (current_bar - self.spike_bar) <= self.max_coil_age:
            if atr_ratio <= self.compression_threshold:
                if not self.coil_armed:
                    self.coil_armed = True
                    self.coil_armed_bar = current_bar

        # Expire stale coils
        if self.coil_armed and (current_bar - self.coil_armed_bar > self.max_coil_age):
            self.coil_armed = False

        # Position exit management (if coil collapses in opposite direction)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            channel_mid = (max(highs[-(self.channel_period + 1):-1]) + min(lows[-(self.channel_period + 1):-1])) / 2.0
            current_close = closes[-1]
            if pos_dir == "long" and current_close < channel_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "coil_long_mean_loss",
                        "close": current_close,
                        "mid": channel_mid,
                        "atr_ratio": round(atr_ratio, 3)
                    }
                )
            elif pos_dir == "short" and current_close > channel_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "coil_short_mean_loss",
                        "close": current_close,
                        "mid": channel_mid,
                        "atr_ratio": round(atr_ratio, 3)
                    }
                )
            return None

        # Check entry cooldown
        if (current_bar - self.last_exit_bar) < self.cooldown_bars:
            return None

        if not self.coil_armed:
            return None

        # 3. Channel breakout trigger (excluding current bar)
        recent_highs = highs[-(self.channel_period + 1):-1]
        recent_lows = lows[-(self.channel_period + 1):-1]
        if len(recent_highs) < self.channel_period:
            return None

        breakout_high = max(recent_highs)
        breakout_low = min(recent_lows)
        current_close = closes[-1]

        # Trend filter from market context
        trend_regime = ctx.market.get("trend_regime", "neutral")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if crisis_score > 0.70:
            return None

        # Long trigger: Break above channel high
        if current_close > breakout_high and trend_regime != "bear":
            self.coil_armed = False
            confidence = 0.75 if trend_regime == "bull" else 0.65
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_long_breakout",
                    "breakout_high": breakout_high,
                    "close": current_close,
                    "atr_ratio": round(atr_ratio, 3),
                    "bars_since_spike": current_bar - self.spike_bar,
                    "trend_regime": trend_regime
                }
            )

        # Short trigger: Break below channel low
        if current_close < breakout_low and trend_regime != "bull":
            self.coil_armed = False
            confidence = 0.75 if trend_regime == "bear" else 0.65
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_short_breakout",
                    "breakout_low": breakout_low,
                    "close": current_close,
                    "atr_ratio": round(atr_ratio, 3),
                    "bars_since_spike": current_bar - self.spike_bar,
                    "trend_regime": trend_regime
                }
            )

        return None