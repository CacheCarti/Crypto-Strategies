from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingSqueezeContrarian(Strategy):
    METADATA = {
        "name": "EthFundingSqueezeContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.extreme_neg_funding = -0.00015  # -0.015% per 8h
        self.extreme_pos_funding = 0.00030   # +0.030% per 8h
        self.persistence_needed = 3

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        closes = ctx.closes(45)
        if len(closes) < 35:
            return None

        # Track persistent funding rate
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Regimes & trend filters
        sma30 = self._sma(closes, 30)
        rsi14 = self._rsi(closes, 14)
        if sma30 is None or rsi14 is None:
            return None

        recent_funding = self.funding_history[-self.persistence_needed:] if len(self.funding_history) >= self.persistence_needed else []

        # Position management: Normalization exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit when funding normalizes back above zero or overbought bounce completed
                if current_funding >= 0.00005 or rsi14 > 65.0:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "funding_normalized_long_exit",
                        "funding": current_funding,
                        "rsi": rsi14
                    })
            elif pos_dir == "short":
                # Exit when funding drops back to neutral or oversold completed
                if current_funding <= -0.00005 or rsi14 < 35.0:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "funding_normalized_short_exit",
                        "funding": current_funding,
                        "rsi": rsi14
                    })
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if len(recent_funding) < self.persistence_needed:
            return None

        # Event trigger: Persistence of funding crowding
        is_persistently_negative = all(f <= self.extreme_neg_funding for f in recent_funding)
        is_persistently_positive = all(f >= self.extreme_pos_funding for f in recent_funding)

        current_price = ctx.bar.close

        # Avoid fading into unchecked momentum (ensure price is not accelerating in the trend)
        # For long squeeze: crowd short (negative funding), price holding or stabilizing (RSI > 32 and price not collapsing far below SMA30)
        dist_from_sma = (current_price - sma30) / sma30

        if is_persistently_negative and -0.04 < dist_from_sma and rsi14 < 45.0:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=300.0,
                take_profit_bps=500.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "funding_short_crowd_squeeze_long",
                    "funding": current_funding,
                    "rsi": rsi14,
                    "dist_sma30": dist_from_sma
                }
            )

        # For short squeeze: crowd heavily long (positive funding), price showing exhaustion (RSI > 55 and not overextending above 4%)
        if is_persistently_positive and dist_from_sma < 0.04 and rsi14 > 55.0:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=300.0,
                take_profit_bps=500.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "funding_long_crowd_fade_short",
                    "funding": current_funding,
                    "rsi": rsi14,
                    "dist_sma30": dist_from_sma
                }
            )

        return None