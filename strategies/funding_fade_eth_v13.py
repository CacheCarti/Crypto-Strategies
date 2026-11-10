from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarian(Strategy):
    METADATA = {
        "name": "EthFundingContrarian",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_threshold = 0.00015  # 0.015% per 8h (extreme threshold)
        self.persistence_needed = 3       # Consecutive bars of extreme funding
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 6            # 6-bar cooldown post exit to prevent re-churn
        self.bars_in_trade = 0

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Track funding history
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > 10:
            self.funding_history.pop(0)

        # Handle active position management / exits
        if ctx.has_position():
            self.bars_in_trade += 1
            pos_dir = ctx.position_direction()

            # Normalize exit condition: funding mean reverts back towards 0
            if pos_dir == "long" and current_funding > -0.00003:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "funding_normalized_long_exit",
                    "funding": current_funding,
                    "bars_held": self.bars_in_trade
                })
            elif pos_dir == "short" and current_funding < 0.00003:
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "funding_normalized_short_exit",
                    "funding": current_funding,
                    "bars_held": self.bars_in_trade
                })
            return None

        # Post-trade cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis regimes to avoid adverse OOS tail risk
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.45 or ctx.regime == "crisis":
            return None

        # Check funding persistence over the last N bars
        if len(self.funding_history) < self.persistence_needed:
            return None
        recent_funding = self.funding_history[-self.persistence_needed:]
        persistent_short_crowd = all(f <= -self.funding_threshold for f in recent_funding)
        persistent_long_crowd = all(f >= self.funding_threshold for f in recent_funding)

        if not (persistent_short_crowd or persistent_long_crowd):
            return None

        # Indicators for price exhaustion & trend filter (don't fade rampant runaway trends)
        ema50 = self._ema(closes, 50)
        rsi = self._rsi(closes, 14)
        if ema50 is None or rsi is None:
            return None

        current_price = ctx.bar.close

        # Long Squeeze: Crowd is aggressively short; price is oversold/stabilizing near or not far extended
        if persistent_short_crowd:
            # Avoid fading if price is in a catastrophic freefall (RSI < 20) or runaway parabolic
            if 25.0 <= rsi <= 55.0 and current_price > closes[-5] * 0.985:
                conf = min(0.9, 0.55 + abs(current_funding) * 1500)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=320.0,
                    take_profit_bps=550.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "persistent_negative_funding_squeeze",
                        "funding": current_funding,
                        "rsi": round(rsi, 2),
                        "ema50": round(ema50, 2),
                        "price": current_price
                    }
                )

        # Short Squeeze Fading: Crowd is aggressively long; price is overbought/exhausting
        if persistent_long_crowd:
            # Overbought condition confirms leverage exhaustion
            if 45.0 <= rsi <= 75.0 and current_price < closes[-5] * 1.015:
                conf = min(0.9, 0.55 + abs(current_funding) * 1500)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=320.0,
                    take_profit_bps=550.0,
                    horizon_seconds=21600,
                    metadata={
                        "reason": "persistent_positive_funding_exhaustion",
                        "funding": current_funding,
                        "rsi": round(rsi, 2),
                        "ema50": round(ema50, 2),
                        "price": current_price
                    }
                )

        return None