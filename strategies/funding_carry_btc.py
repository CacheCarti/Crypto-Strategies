from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcFundingCarrySwing(Strategy):
    METADATA = {
        "name": "BTC Funding Carry Swing",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 55,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -999

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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.slow_ema_period + 15)
        if len(closes) < self.slow_ema_period:
            return None

        current_close = ctx.bar.close
        ema_fast = self._ema(closes, self.fast_ema_period)
        ema_slow = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or rsi is None:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()
        bars_since_trade = ctx.bar_index - self.last_trade_bar

        # Exit / Flatten Logic
        if has_pos:
            if pos_dir == "long":
                if funding > 0.00035 or (current_close < ema_slow and rsi < 42.0):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_carry_exit_or_trend_break",
                            "funding_rate": funding,
                            "rsi": rsi,
                            "close": current_close,
                            "ema_slow": ema_slow,
                        },
                    )
            elif pos_dir == "short":
                if funding < -0.00015 or (current_close > ema_slow and rsi > 58.0):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_carry_exit_or_trend_break",
                            "funding_rate": funding,
                            "rsi": rsi,
                            "close": current_close,
                            "ema_slow": ema_slow,
                        },
                    )
            return None

        # Entry Logic (Gated by cooldown)
        if bars_since_trade < self.cooldown_bars:
            return None

        # Long carry setup: Funding negative / low while price holds above short-term trend
        if funding <= 0.00002 and current_close > ema_fast and 42.0 <= rsi <= 68.0:
            confidence = 0.70
            if funding < -0.00008:
                confidence = 0.85
            elif current_close > ema_slow:
                confidence = 0.75

            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_discount_long_carry",
                    "funding_rate": funding,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "close": current_close,
                },
            )

        # Short carry setup: Overheated positive funding while price loses fast trend
        if funding >= 0.00022 and current_close < ema_fast and 32.0 <= rsi <= 58.0:
            confidence = 0.65
            if funding > 0.00035:
                confidence = 0.80
            elif current_close < ema_slow:
                confidence = 0.72

            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_overheated_short_carry",
                    "funding_rate": funding,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "close": current_close,
                },
            )

        return None