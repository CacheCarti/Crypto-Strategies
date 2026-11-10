from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingRateFadeStrategy(Strategy):
    METADATA = {
        "name": "Funding Rate Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 6 # Check funding over last 6 hours (6 bars)
        self.funding_threshold = 0.0003 # 0.03% funding rate
        self.funding_neutral_threshold = 0.00005 # Funding rate considered normal
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade (12 bars)
        self.last_trade_bar = -self.cooldown_bars

        # For EMA and RSI
        self.ema_fast_period = 10
        self.ema_slow_period = 30
        self.rsi_period = 14
        self.oversold_rsi = 30
        self.overbought_rsi = 70

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Handle division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.rsi_period + 1)
        if len(closes) < self.rsi_period + 1:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_rsi = self._rsi(closes, self.rsi_period)
        if current_rsi is None:
            return None
        
        # Market regime filters
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Funding rate persistence check
        funding_rates = [ctx.features.get(f"funding_rate_ethusdt", 0.0) for _ in range(self.funding_period)]
        if any(f is None for f in funding_rates) or len(funding_rates) < self.funding_period:
            return None

        current_funding = funding_rates[-1]
        
        # Long signal: Extreme negative funding (people paying to short), fade with a long
        if all(f < -self.funding_threshold for f in funding_rates) and current_funding < -self.funding_neutral_threshold:
            if not ctx.has_position():
                # Additional filters for long
                if current_rsi < self.oversold_rsi and regime != "CRISIS" and trend_regime != "bear" and fear_greed < 40:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "extreme_negative_funding_fade_rsi_oversold",
                            "funding_rates_avg": sum(funding_rates) / self.funding_period,
                            "current_funding": current_funding,
                            "rsi": current_rsi,
                            "regime": regime,
                            "trend_regime": trend_regime,
                            "fear_greed": fear_greed
                        }
                    )
            elif ctx.position_direction() == "short":
                # If currently short, flip to long due to strong signal
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    metadata={
                        "reason": "extreme_negative_funding_fade_flip_rsi_oversold",
                        "funding_rates_avg": sum(funding_rates) / self.funding_period,
                        "current_funding": current_funding,
                        "rsi": current_rsi,
                        "regime": regime,
                        "trend_regime": trend_regime,
                        "fear_greed": fear_greed
                    }
                )

        # Short signal: Extreme positive funding (people paying to long), fade with a short
        elif all(f > self.funding_threshold for f in funding_rates) and current_funding > self.funding_neutral_threshold:
            if not ctx.has_position():
                # Additional filters for short
                if current_rsi > self.overbought_rsi and regime != "CRISIS" and trend_regime != "bull" and fear_greed > 60:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "extreme_positive_funding_fade_rsi_overbought",
                            "funding_rates_avg": sum(funding_rates) / self.funding_period,
                            "current_funding": current_funding,
                            "rsi": current_rsi,
                            "regime": regime,
                            "trend_regime": trend_regime,
                            "fear_greed": fear_greed
                        }
                    )
            elif ctx.position_direction() == "long":
                # If currently long, flip to short due to strong signal
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    metadata={
                        "reason": "extreme_positive_funding_fade_flip_rsi_overbought",
                        "funding_rates_avg": sum(funding_rates) / self.funding_period,
                        "current_funding": current_funding,
                        "rsi": current_rsi,
                        "regime": regime,
                        "trend_regime": trend_regime,
                        "fear_greed": fear_greed
                    }
                )

        # Exit condition: Funding normalizes
        if ctx.has_position():
            if abs(current_funding) < self.funding_neutral_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "funding_normalized",
                        "current_funding": current_funding,
                        "entry_direction": ctx.position_direction(),
                        "bars_held": ctx.bar_index - self.last_trade_bar
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # This method is called when a position closes.
        # We already handle cooldown in on_bar, so no further action here.
        pass