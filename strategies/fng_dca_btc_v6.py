from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcFearAccumulator(Strategy):
    METADATA = {
        "name": "BtcFearAccumulator",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.fear_threshold = 32.0
        self.extreme_fear_threshold = 22.0
        self.exit_greed_threshold = 55.0
        self.cooldown_bars = 5
        self.last_exit_bar = -999

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
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

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        sma20 = self._sma(closes, 20)
        current_close = ctx.bar.close
        fg_index = float(ctx.features.get("fear_greed_index", 50.0))
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Exit management
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit condition 1: Sentiment recovered out of fear regime
                if fg_index >= self.exit_greed_threshold:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "fear_discount_exhausted",
                            "fear_greed_index": fg_index,
                            "rsi": round(rsi, 2),
                            "close": current_close,
                        },
                    )
                # Exit condition 2: Technical overbought stretch
                if rsi >= 72.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "rsi_overbought_swing_exit",
                            "fear_greed_index": fg_index,
                            "rsi": round(rsi, 2),
                            "close": current_close,
                        },
                    )
            return None

        # Entry gating: Mandatory post-exit cooldown
        if bars_since_exit < self.cooldown_bars:
            return None

        # Price-based accumulation triggers during fear regimes
        is_extreme_fear = fg_index <= self.extreme_fear_threshold and rsi < 50.0
        is_standard_fear_dip = fg_index <= self.fear_threshold and rsi < 42.0

        if is_extreme_fear or is_standard_fear_dip:
            # Scaled confidence: higher during peak market distress
            fear_intensity = max(0.0, min(1.0, (50.0 - fg_index) / 50.0))
            confidence = min(0.95, max(0.55, 0.55 + 0.35 * fear_intensity))
            trigger_reason = (
                "extreme_fear_deep_discount_entry"
                if is_extreme_fear
                else "fear_regime_oversold_accumulation"
            )

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": trigger_reason,
                    "fear_greed_index": fg_index,
                    "rsi": round(rsi, 2),
                    "sma20": round(sma20, 2) if sma20 else 0.0,
                    "close": current_close,
                    "bars_since_exit": bars_since_exit,
                },
            )

        return None