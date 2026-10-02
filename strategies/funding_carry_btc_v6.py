from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcFundingCarryStrategy(Strategy):
    METADATA = {
        "name": "BTC Funding Rate Carry",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 580.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 12
        self.slow_period = 34
        self.rsi_period = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 2:
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_slow = self._ema(closes, self.slow_period)
        ema_fast_prev = self._ema(closes[:-1], self.fast_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_slow is None or ema_fast_prev is None or rsi is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit if trend breaks, funding turns severely crowded, or RSI is overbought
                if (
                    ema_fast < ema_slow
                    or funding_rate > 0.00035
                    or rsi > 78.0
                    or current_close < ema_slow
                ):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "exit_long_carry_condition",
                            "funding_rate": funding_rate,
                            "rsi": round(rsi, 2),
                            "ema_fast": round(ema_fast, 2),
                            "ema_slow": round(ema_slow, 2),
                            "close": current_close,
                        },
                    )
            elif pos_dir == "short":
                # Exit if trend breaks, funding turns deeply negative, or RSI is oversold
                if (
                    ema_fast > ema_slow
                    or funding_rate < -0.0001
                    or rsi < 24.0
                    or current_close > ema_slow
                ):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "exit_short_carry_condition",
                            "funding_rate": funding_rate,
                            "rsi": round(rsi, 2),
                            "ema_fast": round(ema_fast, 2),
                            "ema_slow": round(ema_slow, 2),
                            "close": current_close,
                        },
                    )
            return None

        # Cooldown guard after closing a trade
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long Carry Setup: Low/negative funding (shorts paying longs) + Bullish EMA structure + Momentum cross
        bullish_cross = prev_close <= ema_fast_prev and current_close > ema_fast
        if funding_rate <= 0.00008 and ema_fast > ema_slow and 42.0 <= rsi <= 68.0 and bullish_cross:
            confidence = 0.70
            if funding_rate <= 0.0:
                confidence = 0.85
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_carry_favorable_funding_ema_breakout",
                    "funding_rate": funding_rate,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "close": current_close,
                },
            )

        # Short Carry Setup: Sustained positive funding (longs paying shorts) + Bearish EMA structure + Breakdown cross
        bearish_cross = prev_close >= ema_fast_prev and current_close < ema_fast
        if funding_rate >= 0.00022 and ema_fast < ema_slow and 32.0 <= rsi <= 58.0 and bearish_cross:
            confidence = 0.65
            if funding_rate >= 0.0004:
                confidence = 0.80
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_carry_high_funding_ema_breakdown",
                    "funding_rate": funding_rate,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "close": current_close,
                },
            )

        return None