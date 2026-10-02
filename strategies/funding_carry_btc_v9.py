from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BTCFundingCarryTilt(Strategy):
    METADATA = {
        "name": "BTC Funding Carry Tilt",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 580.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 45,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 14
        self.slow_period = 34
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_action_bar = -100

        # Funding rate thresholds
        self.funding_long_ceiling = 0.00008   # Below this, long carry is attractive/cheap
        self.funding_short_floor = 0.00016   # Above this, long carry is crowded and short pays

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.last_action_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                # Exit long if momentum strongly exhausts, trend breaks down, or funding gets excessively punitive
                if rsi > 76.0 or current_price < ema_slow or funding > 0.00035:
                    self.last_action_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_carry_exit_or_invalidation",
                            "rsi": rsi,
                            "ema_slow": ema_slow,
                            "funding": funding,
                            "price": current_price,
                        },
                    )
            elif direction == "short":
                # Exit short if momentum rebounds, trend reverses, or funding becomes heavily negative
                if rsi < 24.0 or current_price > ema_slow or funding < -0.00010:
                    self.last_action_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_carry_exit_or_invalidation",
                            "rsi": rsi,
                            "ema_slow": ema_slow,
                            "funding": funding,
                            "price": current_price,
                        },
                    )
            return None

        # Mandatory cooldown guard after trade/exit
        if ctx.bar_index - self.last_action_bar < self.cooldown_bars:
            return None

        # Previous bar metrics for transition triggers
        prev_closes = closes[:-1]
        prev_ema_fast = self._ema(prev_closes, self.fast_period)

        # Long Entry: Favorable funding + upward trend structure + trigger confirmation
        if funding <= self.funding_long_ceiling:
            trend_bullish = ema_fast > ema_slow and current_price > ema_slow
            rsi_sweet_spot = 45.0 < rsi < 65.0
            price_rebound_trigger = (
                prev_ema_fast is not None
                and prev_closes[-1] <= prev_ema_fast
                and current_price > ema_fast
            )

            if trend_bullish and rsi_sweet_spot and price_rebound_trigger:
                self.last_action_bar = ctx.bar_index
                conf = 0.65 if funding < 0.0 else 0.55
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "favorable_funding_ema_pullback_bounce",
                        "funding": funding,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price,
                    },
                )

        # Short Entry: Elevated funding (crowded longs pay shorts) + downward trend structure + trigger confirmation
        if funding >= self.funding_short_floor:
            trend_bearish = ema_fast < ema_slow and current_price < ema_slow
            rsi_sweet_spot = 35.0 < rsi < 55.0
            price_rejection_trigger = (
                prev_ema_fast is not None
                and prev_closes[-1] >= prev_ema_fast
                and current_price < ema_fast
            )

            if trend_bearish and rsi_sweet_spot and price_rejection_trigger:
                self.last_action_bar = ctx.bar_index
                conf = 0.70 if funding > 0.00025 else 0.58
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "crowded_funding_ema_rejection",
                        "funding": funding,
                        "rsi": rsi,
                        "ema_fast": ema_fast,
                        "ema_slow": ema_slow,
                        "price": current_price,
                    },
                )

        return None