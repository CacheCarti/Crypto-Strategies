from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateReversion(Strategy):
    METADATA = {
        "name": "FundingRateReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16
        self.extreme_long_funding = -0.00025
        self.extreme_short_funding = 0.00035
        self.funding_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        ema_trend = self._ema(closes, 30)
        rsi = self._rsi(closes, 14)
        if ema_trend is None or rsi is None:
            return None

        price = ctx.bar.close

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            # Normalization exit
            if pos_dir == "long" and current_funding >= -0.00005:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_long_exit",
                    "funding": current_funding,
                    "bars_held": bars_held
                })
            elif pos_dir == "short" and current_funding <= 0.00005:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_short_exit",
                    "funding": current_funding,
                    "bars_held": bars_held
                })

            # Time-based defensive exit
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_hold_time_exceeded",
                    "bars_held": bars_held
                })

            return None

        # Mandatory Cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if len(self.funding_history) < 3:
            return None

        # Check persistence across last 3 bars
        persistent_negative = all(f <= self.extreme_long_funding for f in self.funding_history[-3:])
        persistent_positive = all(f >= self.extreme_short_funding for f in self.funding_history[-3:])

        # Avoid fading strong adverse trends
        # Long condition: crowd is paying heavy short funding, price not deeply dumped/oversold
        if persistent_negative and price >= (ema_trend * 0.985) and rsi >= 35.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                metadata={
                    "reason": "persistent_short_crowd_squeeze",
                    "funding": current_funding,
                    "rsi": rsi,
                    "price": price,
                    "ema_trend": ema_trend
                }
            )

        # Short condition: crowd is paying heavy long funding, price not in vertical parabolic breakout
        if persistent_positive and price <= (ema_trend * 1.015) and rsi <= 65.0:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                metadata={
                    "reason": "persistent_long_crowd_fade",
                    "funding": current_funding,
                    "rsi": rsi,
                    "price": price,
                    "ema_trend": ema_trend
                }
            )

        return None