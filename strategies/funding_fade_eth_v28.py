from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 6
        self.persistence_needed = 3
        self.funding_threshold = 0.00015
        self.entry_bar = 0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        if len(self.funding_history) < self.persistence_needed:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        sma20 = self._sma(closes, 20)
        if sma20 is None:
            return None

        price = ctx.bar.close

        # Volatility-adjusted stop-loss and take-profit
        atr_bps = (atr / price) * 10000.0
        sl_bps = max(140.0, min(300.0, atr_bps * 1.6))
        tp_bps = max(220.0, min(480.0, atr_bps * 2.4))

        # Manage existing open positions
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Timed structural exit after 16 bars (16h hold limit)
            if bars_held >= 16:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "timed_structural_exit",
                    "bars_held": bars_held,
                    "funding": current_funding
                })

            # Normalization exit: crowd positioning has neutralized
            if direction == "long" and current_funding >= 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "funding_normalized_long_exit",
                    "funding": current_funding,
                    "bars_held": bars_held
                })
            elif direction == "short" and current_funding <= 0.0:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "funding_normalized_short_exit",
                    "funding": current_funding,
                    "bars_held": bars_held
                })

            return None

        # Mandatory cooldown check after an exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        recent_funding = self.funding_history[-self.persistence_needed:]
        persistent_negative = all(f <= -self.funding_threshold for f in recent_funding)
        persistent_positive = all(f >= self.funding_threshold for f in recent_funding)

        # Volatility stretch filter: avoid fading a vertical blowout trend
        # Ensure price is reasonably within 2.5 ATRs of SMA20
        dist_sma = abs(price - sma20)
        trend_extended = dist_sma > (2.5 * atr)

        if persistent_negative and not trend_extended:
            # Squeeze crowded shorts: price should not be crashing through floor
            if price >= sma20 - (1.8 * atr):
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "persistent_negative_funding_squeeze",
                        "funding": current_funding,
                        "atr_bps": atr_bps,
                        "price": price,
                        "sma20": sma20
                    }
                )

        if persistent_positive and not trend_extended:
            # Squeeze crowded longs: price should not be skyrocketing uncontrollably
            if price <= sma20 + (1.8 * atr):
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "persistent_positive_funding_exhaustion",
                        "funding": current_funding,
                        "atr_bps": atr_bps,
                        "price": price,
                        "sma20": sma20
                    }
                )

        return None