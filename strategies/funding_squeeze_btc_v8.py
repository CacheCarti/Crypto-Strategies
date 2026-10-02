from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_return = 18
        self.funding_window = 8
        self.rsi_period = 14
        self.ema_period = 20
        self.cooldown_bars = 6
        self.last_entry_bar = -100
        self.last_exit_bar = -100
        self.funding_history = []

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 30:
            self.funding_history.pop(0)

        # Average recent funding rate
        recent_funding = self.funding_history[-self.funding_window:]
        avg_funding = sum(recent_funding) / len(recent_funding)

        current_price = closes[-1]
        past_price = closes[-self.lookback_return]
        price_ret = (current_price - past_price) / past_price

        rsi = self._rsi(closes, self.rsi_period)
        ema20 = self._ema(closes, self.ema_period)
        if rsi is None or ema20 is None:
            return None

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long":
                # Exit if momentum reverses sharply or price breaks well below EMA20
                if rsi > 74.0 or current_price < ema20 * 0.988:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_momentum_exhaustion" if rsi > 74.0 else "long_ema_invalidation",
                            "rsi": round(rsi, 2),
                            "ema20": round(ema20, 2),
                            "current_price": round(current_price, 2),
                            "avg_funding": round(avg_funding, 6),
                        }
                    )
            elif direction == "short":
                # Exit if oversold or price breaks well above EMA20
                if rsi < 26.0 or current_price > ema20 * 1.012:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_momentum_exhaustion" if rsi < 26.0 else "short_ema_invalidation",
                            "rsi": round(rsi, 2),
                            "ema20": round(ema20, 2),
                            "current_price": round(current_price, 2),
                            "avg_funding": round(avg_funding, 6),
                        }
                    )
            return None

        # Cooldown guard after entry or exit
        if (ctx.bar_index - self.last_entry_bar < self.cooldown_bars) or \
           (ctx.bar_index - self.last_exit_bar < self.cooldown_bars):
            return None

        # Long Squeeze: Price is expanding upward while funding is negative/depressed (shorts trapped)
        is_long_squeeze = (
            price_ret >= 0.012 and
            avg_funding <= -0.00001 and
            current_price > ema20 and
            48.0 <= rsi <= 68.0
        )

        # Short Squeeze / Liquidation Cascade: Price falling while funding is positive/crowded (longs trapped)
        is_short_squeeze = (
            price_ret <= -0.012 and
            avg_funding >= 0.00008 and
            current_price < ema20 and
            32.0 <= rsi <= 52.0
        )

        if is_long_squeeze:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.9, 0.65 + abs(avg_funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation_negative_funding",
                    "price_ret_18": round(price_ret * 100, 3),
                    "avg_funding": round(avg_funding, 6),
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20, 2),
                    "current_price": round(current_price, 2),
                }
            )

        if is_short_squeeze:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.9, 0.65 + avg_funding * 1000.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_liquidation_continuation_positive_funding",
                    "price_ret_18": round(price_ret * 100, 3),
                    "avg_funding": round(avg_funding, 6),
                    "rsi": round(rsi, 2),
                    "ema20": round(ema20, 2),
                    "current_price": round(current_price, 2),
                }
            )

        return None