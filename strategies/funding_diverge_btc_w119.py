from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingDivergence(Strategy):
    METADATA = {
        "name": "Funding Divergence BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Use 24-hour funding average
        self.price_ema_period = 20
        self.price_change_period = 3
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        closes = ctx.closes(self.price_ema_period + self.price_change_period)
        if len(closes) < self.price_ema_period + self.price_change_period:
            return None

        current_close = ctx.bar.close
        prev_closes = ctx.closes(self.price_change_period + 1)
        if len(prev_closes) < self.price_change_period + 1:
            return None

        # Price change over the last N bars
        price_change_pct = (current_close - prev_closes[0]) / prev_closes[0] * 100

        # Funding rate data
        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_period)]
        # This is a simplification. In a real scenario, you'd want historical funding rates.
        # For backtesting, ctx.features.get("funding_rate_btcusdt", 0.0) will give you the current bar's funding rate.
        # To get an average, we'll assume the feature provides the value for 'now' and we need to average past ones.
        # Since ctx.features does not provide historical feature values, we use the current one as a proxy for the 'average'
        # or we'd need to assume the feature provides an already averaged value.
        # For this exercise, we will check the current funding rate and assume a recent trend.
        current_funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Simple Price EMA for trend confirmation
        price_ema = self._ema(closes, self.price_ema_period)
        if price_ema is None:
            return None

        # Strategy Logic: Price/Funding Divergence
        # Long condition: Price rising, but funding rate is negative (suggests short squeeze/undervalued)
        # Short condition: Price falling, but funding rate is positive (suggests long liquidation/overvalued)

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        if not ctx.has_position():
            # Long setup: Price rising, funding negative
            if price_change_pct > 0.5 and current_funding_rate < -0.0001: # 0.5% price rise, -0.01% funding
                signal_direction = "long"
                confidence = min(1.0, 0.5 + abs(current_funding_rate) * 1000 + price_change_pct * 0.1)
                reason = "price_rising_negative_funding_divergence"
                metadata = {
                    "price_change_pct": price_change_pct,
                    "funding_rate": current_funding_rate,
                    "current_price": current_close,
                    "price_ema": price_ema
                }
            # Short setup: Price falling, funding positive
            elif price_change_pct < -0.5 and current_funding_rate > 0.0001: # 0.5% price fall, +0.01% funding
                signal_direction = "short"
                confidence = min(1.0, 0.5 + current_funding_rate * 1000 + abs(price_change_pct) * 0.1)
                reason = "price_falling_positive_funding_divergence"
                metadata = {
                    "price_change_pct": price_change_pct,
                    "funding_rate": current_funding_rate,
                    "current_price": current_close,
                    "price_ema": price_ema
                }

            if signal_direction:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata=metadata
                )

        # If holding a position, just hold
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar for cooldown
        self.last_trade_bar = ctx.bar_index
        # You could implement adaptive sizing or other logic here based on position performance
        pass