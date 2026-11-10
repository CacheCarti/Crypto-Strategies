from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "ETH Funding Contrarian Squeeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_long_thresh = -0.00010
        self.funding_short_thresh = 0.00025
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.funding_history = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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

        if len(self.funding_history) < 3:
            return None

        avg_funding_3 = sum(self.funding_history[-3:]) / 3.0
        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema20 is None or rsi is None:
            return None

        current_close = ctx.bar.close

        # Position management: Active exit if funding normalizes back to neutral zone
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and avg_funding_3 >= 0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized_long_exit",
                        "avg_funding": avg_funding_3,
                        "close": current_close,
                        "rsi": rsi,
                    },
                )
            elif pos_dir == "short" and avg_funding_3 <= -0.00002:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized_short_exit",
                        "avg_funding": avg_funding_3,
                        "close": current_close,
                        "rsi": rsi,
                    },
                )
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Regime safety guard: avoid opening counter-trend during extreme crisis / meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Signal: Persistent negative funding (crowd paying to short) + price stabilizing (RSI > 35, close not falling hard off EMA)
        if avg_funding_3 <= self.funding_long_thresh:
            if rsi > 35.0 and current_close >= ema20 * 0.985:
                confidence = min(1.0, 0.65 + abs(avg_funding_3) * 1000)
                return ctx.signal(
                    "long",
                    confidence=round(confidence, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_negative_squeeze_long",
                        "avg_funding_3": avg_funding_3,
                        "rsi": rsi,
                        "ema20": ema20,
                        "close": current_close,
                    },
                )

        # Short Signal: Persistent positive funding (crowd over-leveraged long) + price exhausted (RSI < 65, close not exploding above EMA)
        elif avg_funding_3 >= self.funding_short_thresh:
            if rsi < 65.0 and current_close <= ema20 * 1.015:
                confidence = min(1.0, 0.65 + abs(avg_funding_3) * 1000)
                return ctx.signal(
                    "short",
                    confidence=round(confidence, 2),
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "funding_elevated_fade_short",
                        "avg_funding_3": avg_funding_3,
                        "rsi": rsi,
                        "ema20": ema20,
                        "close": current_close,
                    },
                )

        return None