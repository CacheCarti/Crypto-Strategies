from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,  # 4 hours intended avg hold on 1h bars
        "warmup_bars": 35,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.max_history = 10
        self.cooldown_remaining = 0
        self.bars_in_position = 0
        self.pos_direction = None

        # Parameters calibrated for 1h ETH swing & high conviction
        self.funding_long_thresh = -0.00015  # -0.015% per 8h (crowd heavily short)
        self.funding_short_thresh = 0.00030  # +0.030% per 8h (crowd heavily long)
        self.persistence_needed = 2          # consecutive bars extreme funding
        self.cooldown_period = 4             # wait 4 bars (~4h) after exit

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
        self.cooldown_remaining = self.cooldown_period
        self.bars_in_position = 0
        self.pos_direction = None

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        # Track funding rate history
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.max_history:
            self.funding_history.pop(0)

        # Handle cooldown countdown if out of position
        if not ctx.has_position():
            if self.cooldown_remaining > 0:
                self.cooldown_remaining -= 1
                return None
        else:
            self.bars_in_position += 1

        # Indicators
        ema20 = self._ema(closes, 20)
        rsi14 = self._rsi(closes, 14)
        if ema20 is None or rsi14 is None:
            return None

        curr_close = closes[-1]
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # POSITION MANAGEMENT / EXIT LOGIC
        if ctx.has_position():
            direction = ctx.position_direction()
            # 1. Exit when funding normalizes (mean-reversion completed)
            if direction == "long" and current_funding >= 0.00002:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_long_exit",
                    "funding": current_funding,
                    "price": curr_close,
                    "bars_held": self.bars_in_position
                })
            elif direction == "short" and current_funding <= -0.00002:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "funding_normalized_short_exit",
                    "funding": current_funding,
                    "price": curr_close,
                    "bars_held": self.bars_in_position
                })

            # 2. Safety exit if market enters crisis meltdown
            if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.7:
                return ctx.signal("flat", confidence=0.9, metadata={
                    "reason": "crisis_regime_exit",
                    "regime": market_regime,
                    "crisis_score": crisis_score
                })

            return None

        # ENTRY LOGIC (Only when flat and cooldown expired)
        # Avoid fading during chaotic market meltdowns
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        if len(self.funding_history) < self.persistence_needed:
            return None

        recent_funding = self.funding_history[-self.persistence_needed:]
        persistent_negative = all(f <= self.funding_long_thresh for f in recent_funding)
        persistent_positive = all(f >= self.funding_short_thresh for f in recent_funding)

        # LONG SETUP: Shorts heavily paying & persistent, but price not in free-fall (RSI > 25, price near or above EMA20*0.98)
        if persistent_negative and rsi14 > 25.0 and curr_close > (ema20 * 0.98):
            conf = 0.75 if curr_close > ema20 else 0.65
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_short_squeeze_setup",
                    "funding_rate": current_funding,
                    "rsi": round(rsi14, 2),
                    "ema20": round(ema20, 2),
                    "price": round(curr_close, 2)
                }
            )

        # SHORT SETUP: Longs heavily paying & persistent, but price not in runaway parabolic pump (RSI < 75, price near or below EMA20*1.02)
        if persistent_positive and rsi14 < 75.0 and curr_close < (ema20 * 1.02):
            conf = 0.75 if curr_close < ema20 else 0.65
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "funding_long_exhaustion_setup",
                    "funding_rate": current_funding,
                    "rsi": round(rsi14, 2),
                    "ema20": round(ema20, 2),
                    "price": round(curr_close, 2)
                }
            )

        return None