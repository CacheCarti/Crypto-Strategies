from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingDivergenceEth(Strategy):
    METADATA = {
        "name": "Funding Divergence ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # ~3 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 2  # funding is hourly, so 2 bars for 2 hours change
        self.price_ema_period = 20
        self.price_std_period = 20
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.price_ema_period + self.funding_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        price_ema = self._ema(closes, self.price_ema_period)
        price_std = self._stddev(closes, self.price_std_period)

        if price_ema is None or price_std is None:
            return None

        # Price deviation from EMA (Z-score like)
        price_deviation = (current_price - price_ema) / price_std if price_std != 0 else 0.0

        # Funding rate change over the last few bars
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        previous_funding_rates = [ctx.features.get(f"funding_rate_ethusdt", 0.0, offset=-i) for i in range(self.funding_period, 0, -1)]
        
        if any(f is None for f in previous_funding_rates) or len(previous_funding_rates) < self.funding_period:
            return None
        
        # Calculate average funding rate change
        funding_rate_change = current_funding - previous_funding_rates[0]

        signal_direction = None
        confidence = 0.0
        reason = ""
        signal_metadata = {}

        # Regime filter: Only trade in normal or volatile regimes, avoid crisis
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Trend filter: Only trade against weak trends or in neutral
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        
        # Long signal:
        # Price is slightly below EMA (or consolidating), indicating potential for bounce/squeeze
        # Funding rate is negative and potentially becoming more negative, or negative and showing signs of exhaustion (less negative)
        # Suggests shorts are in control but price not collapsing, ripe for a short squeeze
        if not ctx.has_position():
            if (
                price_deviation < -0.5 and price_deviation > -1.5 and # Price slightly oversold
                current_funding < -0.00005 and # Negative funding
                funding_rate_change < 0.00001 and # Funding staying negative or becoming less negative (exhaustion of short pressure)
                (trend_regime == "neutral" or (trend_regime == "bear" and trend_confidence < 0.4)) # Not strong bull trend
            ):
                signal_direction = "long"
                confidence = 0.65 + abs(price_deviation) * 0.1 # Higher deviation, higher confidence
                reason = "funding_divergence_long_squeeze_setup"
                signal_metadata = {
                    "price_deviation": price_deviation,
                    "current_funding": current_funding,
                    "funding_rate_change": funding_rate_change,
                    "trend_regime": trend_regime,
                    "trend_confidence": trend_confidence
                }

            # Short signal:
            # Price is slightly above EMA (or consolidating), indicating potential for weakness/liquidation
            # Funding rate is positive and potentially becoming more positive, or positive and showing signs of exhaustion (less positive)
            # Suggests longs are in control but price not surging, ripe for a long liquidation
            elif (
                price_deviation > 0.5 and price_deviation < 1.5 and # Price slightly overbought
                current_funding > 0.00005 and # Positive funding
                funding_rate_change > -0.00001 and # Funding staying positive or becoming less positive (exhaustion of long pressure)
                (trend_regime == "neutral" or (trend_regime == "bull" and trend_confidence < 0.4)) # Not strong bear trend
            ):
                signal_direction = "short"
                confidence = 0.65 + abs(price_deviation) * 0.1 # Higher deviation, higher confidence
                reason = "funding_divergence_short_liquidation_setup"
                signal_metadata = {
                    "price_deviation": price_deviation,
                    "current_funding": current_funding,
                    "funding_rate_change": funding_rate_change,
                    "trend_regime": trend_regime,
                    "trend_confidence": trend_confidence
                }

        # Exit signal:
        # If funding rate flips sign or price moves strongly against the position, exit
        elif ctx.has_position():
            if ctx.position_direction() == "long":
                if current_funding > 0.00005 or price_deviation > 2.0: # Funding turns positive or price gets too extended
                    signal_direction = "flat"
                    confidence = 0.7
                    reason = "long_exit_funding_flipped_or_overextended"
                    signal_metadata = {
                        "current_funding": current_funding,
                        "price_deviation": price_deviation
                    }
            elif ctx.position_direction() == "short":
                if current_funding < -0.00005 or price_deviation < -2.0: # Funding turns negative or price gets too extended
                    signal_direction = "flat"
                    confidence = 0.7
                    reason = "short_exit_funding_flipped_or_overextended"
                    signal_metadata = {
                        "current_funding": current_funding,
                        "price_deviation": price_deviation
                    }

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={"reason": reason, **signal_metadata}
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic based on position close outcomes
        pass