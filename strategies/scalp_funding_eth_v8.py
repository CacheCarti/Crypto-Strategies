from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingTiltScalper(Strategy):
    METADATA = {
        "name": "Funding Tilt Scalper",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 140.0,
        "declared_tp_bps": 240.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 10
        self.rsi_oversold = 22.0
        self.rsi_overbought = 78.0
        self.cooldown_bars = 36  # Hard 3-hour cooldown to prevent trade churn
        self.max_hold_bars = 18   # Maximum hold ~90 mins on 5m bars
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _rsi(self, closes: list, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.rsi_period + 5)
        if len(closes) < self.rsi_period + 1:
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_price = ctx.bar.close

        # Position Management & Mean-Reversion Exits
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Long exit on mean reversion cross back over 50 or time expiry
            if direction == "long":
                if rsi >= 52.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "rsi_reversion_long_exit",
                            "rsi": round(rsi, 2),
                            "funding": round(funding, 6),
                            "price": round(current_price, 2),
                            "bars_held": bars_held,
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "scalp_time_expiry_exit",
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )

            # Short exit on mean reversion cross back below 50 or time expiry
            elif direction == "short":
                if rsi <= 48.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "rsi_reversion_short_exit",
                            "rsi": round(rsi, 2),
                            "funding": round(funding, 6),
                            "price": round(current_price, 2),
                            "bars_held": bars_held,
                        },
                    )
                if bars_held >= self.max_hold_bars:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "scalp_time_expiry_exit",
                            "rsi": round(rsi, 2),
                            "bars_held": bars_held,
                        },
                    )

            return None

        # Mandatory Cooldown Enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entry during extreme market distress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Entry: Negative funding gate (shorts paying longs) + severe RSI exhaustion dip
        if funding < -0.00008 and rsi < self.rsi_oversold:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.8,
                stop_loss_bps=140.0,
                take_profit_bps=240.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "negative_funding_deep_rsi_dip_scalp",
                    "rsi": round(rsi, 2),
                    "funding_rate": round(funding, 6),
                    "price": round(current_price, 2),
                    "regime": regime,
                },
            )

        # Short Entry: Elevated positive funding gate (overcrowded longs) + severe RSI exhaustion rally
        if funding > 0.00018 and rsi > self.rsi_overbought:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.8,
                stop_loss_bps=140.0,
                take_profit_bps=240.0,
                horizon_seconds=1800,
                metadata={
                    "reason": "crowded_positive_funding_deep_rsi_rally_scalp",
                    "rsi": round(rsi, 2),
                    "funding_rate": round(funding, 6),
                    "price": round(current_price, 2),
                    "regime": regime,
                },
            )

        return None