from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "FundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_period = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.funding_threshold = 0.00003  # 0.003% (meaningful tilt away from neutral)
        self.price_move_threshold = 0.006  # 0.6% move over 24h

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
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Check market crisis regime filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_exit", "crisis_score": crisis_score})
            return None

        current_price = closes[-1]
        price_24h_ago = closes[-self.lookback]
        price_change_pct = (current_price - price_24h_ago) / price_24h_ago
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Time-in-position based exit to keep trades independent and dynamic
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            rsi = self._rsi(closes, period=14) or 50.0

            # Momentum exhaustion or divergence breakdown exit
            if ctx.position_direction() == "long":
                if rsi > 72.0 or bars_held >= 12 or funding > 0.00015:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_exhaustion_or_time_exit",
                        "rsi": rsi,
                        "bars_held": bars_held,
                        "funding": funding,
                        "price": current_price
                    })
            elif ctx.position_direction() == "short":
                if rsi < 28.0 or bars_held >= 12 or funding < -0.00015:
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_exhaustion_or_time_exit",
                        "rsi": rsi,
                        "bars_held": bars_held,
                        "funding": funding,
                        "price": current_price
                    })
            return None

        # Mandatory cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        rsi = self._rsi(closes, period=14)
        if rsi is None:
            return None

        # 1. Bullish Squeeze Setup:
        # Price is rising (+0.6% over 24h) but funding is distinctly negative (< -0.003%)
        # Indicates trapped aggressive shorts getting run over. RSI confirmation ensures momentum isn't already peaked.
        if price_change_pct > self.price_move_threshold and funding < -self.funding_threshold and 45.0 < rsi < 68.0:
            self.entry_bar = ctx.bar_index
            confidence = min(1.0, 0.6 + abs(funding) * 2000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "price_change_24h": price_change_pct,
                    "funding_rate": funding,
                    "rsi": rsi,
                    "current_price": current_price
                }
            )

        # 2. Bearish Long-Liquidation Cascade:
        # Price is falling (-0.6% over 24h) but funding remains stubbornly positive (> +0.003%)
        # Trapped stubborn longs forced to liquidate into lower bids.
        if price_change_pct < -self.price_move_threshold and funding > self.funding_threshold and 32.0 < rsi < 55.0:
            self.entry_bar = ctx.bar_index
            confidence = min(1.0, 0.6 + abs(funding) * 2000.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=380.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "long_squeeze_breakdown",
                    "price_change_24h": price_change_pct,
                    "funding_rate": funding,
                    "rsi": rsi,
                    "current_price": current_price
                }
            )

        return None