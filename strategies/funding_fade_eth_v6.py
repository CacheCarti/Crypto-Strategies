from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class FundingRateReversion(Strategy):
    METADATA = {
        "name": "FundingRateContrarianSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history: List[float] = []
        self.last_exit_bar = -999
        self.cooldown_bars = 4
        self.funding_persist_bars = 3
        self.funding_short_thresh = 0.00025  # +0.025% per 8h
        self.funding_long_thresh = -0.00012  # -0.012% per 8h
        self.rsi_period = 14
        self.ema_period = 21

    def _ema(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        if len(self.funding_history) < self.funding_persist_bars:
            return None

        recent_funding = self.funding_history[-self.funding_persist_bars :]
        avg_funding = sum(recent_funding) / len(recent_funding)

        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        if rsi is None or ema is None:
            return None

        current_close = closes[-1]
        bars_since_exit = ctx.bar_index - self.last_exit_bar

        # Position Management & Exit Rules
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit when funding normalizes or RSI gets overbought
                if avg_funding >= 0.00005 or rsi >= 68.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_exit_funding_normalized_or_rsi_high",
                            "avg_funding": avg_funding,
                            "current_funding": current_funding,
                            "rsi": rsi,
                            "price": current_close,
                        },
                    )
            elif pos_dir == "short":
                # Exit when funding normalizes or RSI gets oversold
                if avg_funding <= -0.00003 or rsi <= 32.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_exit_funding_normalized_or_rsi_low",
                            "avg_funding": avg_funding,
                            "current_funding": current_funding,
                            "rsi": rsi,
                            "price": current_close,
                        },
                    )
            return None

        # Cooldown guard
        if bars_since_exit < self.cooldown_bars:
            return None

        # Long Squeeze Setup: crowd heavily short and paying, not in terminal waterfall
        funding_persistently_negative = all(f <= self.funding_long_thresh * 0.8 for f in recent_funding)
        if (
            funding_persistently_negative
            and avg_funding <= self.funding_long_thresh
            and 28.0 < rsi < 58.0
            and current_close > ema * 0.955
        ):
            confidence = min(0.9, 0.60 + abs(avg_funding) * 1000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_negative_squeeze_entry",
                    "avg_funding": avg_funding,
                    "current_funding": current_funding,
                    "rsi": rsi,
                    "ema21": ema,
                    "price": current_close,
                },
            )

        # Short Fade Setup: crowd excessively long and paying, not in parabolic breakout
        funding_persistently_positive = all(f >= self.funding_short_thresh * 0.8 for f in recent_funding)
        if (
            funding_persistently_positive
            and avg_funding >= self.funding_short_thresh
            and 42.0 < rsi < 72.0
            and current_close < ema * 1.045
        ):
            confidence = min(0.9, 0.60 + avg_funding * 800.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_positive_fade_entry",
                    "avg_funding": avg_funding,
                    "current_funding": current_funding,
                    "rsi": rsi,
                    "ema21": ema,
                    "price": current_close,
                },
            )

        return None