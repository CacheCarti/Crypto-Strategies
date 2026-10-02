from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_return = 22
        self.rsi_period = 14
        self.ema_period = 16
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.min_return_threshold = 0.012  # 1.2% price move over lookback

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1.0)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_return + 5)
        if len(closes) < self.lookback_return + 2:
            return None

        current_price = ctx.bar.close
        ref_price = closes[-self.lookback_return]
        price_ret = (current_price - ref_price) / ref_price

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi_val = self._rsi(closes, self.rsi_period)
        ema_val = self._ema(closes, self.ema_period)

        if rsi_val is None or ema_val is None:
            return None

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Check exits for open positions
        if has_pos:
            if pos_dir == "long":
                # Exit if overbought or price drops below EMA or funding turns heavily positive
                if rsi_val > 76.0 or current_price < ema_val * 0.995 or funding > 0.0003:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_squeeze_exhaustion_or_ema_break",
                            "rsi": rsi_val,
                            "price": current_price,
                            "ema": ema_val,
                            "funding": funding,
                        },
                    )
            elif pos_dir == "short":
                # Exit if oversold or price rises above EMA or funding turns heavily negative
                if rsi_val < 24.0 or current_price > ema_val * 1.005 or funding < -0.0002:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_squeeze_exhaustion_or_ema_break",
                            "rsi": rsi_val,
                            "price": current_price,
                            "ema": ema_val,
                            "funding": funding,
                        },
                    )
            return None

        # Mandatory cooldown check
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Setup 1: Short Squeeze Continuation (Price rising while funding is negative)
        # Trapped shorts are forced to cover, pushing price further up
        if price_ret >= self.min_return_threshold and funding <= -0.00002:
            if 42.0 <= rsi_val <= 68.0 and current_price > ema_val:
                self.last_entry_bar = ctx.bar_index
                conf = min(0.85, 0.60 + abs(funding) * 2000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=280.0,
                    take_profit_bps=480.0,
                    metadata={
                        "reason": "short_squeeze_divergence_long",
                        "price_ret_22": price_ret,
                        "funding_rate": funding,
                        "rsi": rsi_val,
                        "ema16": ema_val,
                        "price": current_price,
                    },
                )

        # Setup 2: Long Squeeze Cascade (Price falling while funding is positive)
        # Overleveraged longs are forced to liquidate, accelerating downside
        if price_ret <= -self.min_return_threshold and funding >= 0.00008:
            if 32.0 <= rsi_val <= 58.0 and current_price < ema_val:
                self.last_entry_bar = ctx.bar_index
                conf = min(0.85, 0.60 + funding * 1500.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=280.0,
                    take_profit_bps=480.0,
                    metadata={
                        "reason": "long_squeeze_divergence_short",
                        "price_ret_22": price_ret,
                        "funding_rate": funding,
                        "rsi": rsi_val,
                        "ema16": ema_val,
                        "price": current_price,
                    },
                )

        return None