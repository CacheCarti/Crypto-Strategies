from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeMomentum(Strategy):
    METADATA = {
        "name": "Funding Squeeze Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.rsi_period = 14

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
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Hard cooldown check post-exit or post-entry
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback]
        pct_change = (current_close - past_close) / past_close

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        # Check existing position exit logic
        if ctx.has_position():
            direction = ctx.position_direction()
            bars_held = ctx.bar_index - self.last_entry_bar
            # Exit if momentum reverses or squeeze dissipated
            if direction == "long" and (rsi > 78.0 or pct_change < -0.005):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_momentum_fade", "rsi": rsi, "pct_change": pct_change})
            if direction == "short" and (rsi < 22.0 or pct_change > 0.005):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_momentum_fade", "rsi": rsi, "pct_change": pct_change})
            return None

        # Squeeze Setup 1: Price pushing UP while funding is NEGATIVE (Shorts trapped)
        # Require decisive price momentum (>1.5% over 24h) and confirmation by RSI > 52 (not overbought < 72)
        if pct_change >= 0.015 and funding <= -0.00005 and 52.0 <= rsi <= 72.0:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.9, 0.6 + abs(funding) * 2000.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding": funding,
                    "price_change_24b": round(pct_change * 100, 2),
                    "rsi": round(rsi, 2),
                }
            )

        # Squeeze Setup 2: Price pushing DOWN while funding is POSITIVE (Longs trapped)
        # Require decisive downward drift (<-1.5% over 24h) and confirmation by RSI < 48 (not oversold > 28)
        if pct_change <= -0.015 and funding >= 0.00015 and 28.0 <= rsi <= 48.0:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.9, 0.6 + funding * 1500.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=220.0,
                take_profit_bps=440.0,
                metadata={
                    "reason": "long_squeeze_liquidation",
                    "funding": funding,
                    "price_change_24b": round(pct_change * 100, 2),
                    "rsi": round(rsi, 2),
                }
            )

        return None