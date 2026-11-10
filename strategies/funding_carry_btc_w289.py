from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingCarry(Strategy):
    METADATA = {
        "name": "BTC Funding Carry Strategy",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24
        self.ema_period = 20
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None # Cooldown period

        closes = ctx.closes(self.ema_period + 1)
        if len(closes) < self.ema_period + 1:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]

        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_period)]
        # Simple average of recent funding rates for a smoother signal
        avg_funding_rate = sum(funding_rates) / self.funding_period if self.funding_period > 0 else 0.0

        # Funding rate thresholds (example values, tune as needed)
        # Negative funding: short sellers pay longs, bullish for spot/longs
        # Positive funding: longs pay shorts, bearish for spot/shorts
        funding_long_threshold = -0.00015  # e.g., -0.015% per 8h
        funding_short_threshold = 0.00015   # e.g., +0.015% per 8h

        signal_direction = None
        reason = ""
        confidence = 0.0

        # Entry logic
        if not ctx.has_position():
            # Long entry condition:
            # 1. Average funding rate is significantly negative (longs are getting paid)
            # 2. Price is above EMA or recently crossed above (momentum confirmation)
            # 3. Price confirmation: current close is higher than previous close
            if avg_funding_rate < funding_long_threshold and \
               current_close > ema and \
               current_close > prev_close:
                signal_direction = "long"
                reason = "funding_carry_long_ema_confirm"
                confidence = 0.6 + min(0.4, abs(avg_funding_rate / funding_long_threshold)) # Higher confidence for stronger funding
            
            # Short entry condition:
            # 1. Average funding rate is significantly positive (shorts are getting paid)
            # 2. Price is below EMA or recently crossed below (momentum confirmation)
            # 3. Price confirmation: current close is lower than previous close
            elif avg_funding_rate > funding_short_threshold and \
                 current_close < ema and \
                 current_close < prev_close:
                signal_direction = "short"
                reason = "funding_carry_short_ema_confirm"
                confidence = 0.6 + min(0.4, abs(avg_funding_rate / funding_short_threshold)) # Higher confidence for stronger funding

        # Exit logic (normalize carry condition)
        elif ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit long if funding normalizes (becomes less negative or positive)
                # Or if price crosses below EMA
                if avg_funding_rate >= -0.00005 or current_close < ema: # e.g., -0.005% per 8h (near zero)
                    signal_direction = "flat"
                    reason = "funding_carry_long_exit_normalization"
                    confidence = 0.7 # High confidence to exit when conditions change
            elif ctx.position_direction() == "short":
                # Exit short if funding normalizes (becomes less positive or negative)
                # Or if price crosses above EMA
                if avg_funding_rate <= 0.00005 or current_close > ema: # e.g., +0.005% per 8h (near zero)
                    signal_direction = "flat"
                    reason = "funding_carry_short_exit_normalization"
                    confidence = 0.7 # High confidence to exit when conditions change

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "avg_funding_rate": avg_funding_rate,
                    "current_close": current_close,
                    "ema_value": ema,
                    "regime": ctx.regime,
                },
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for analysis
        pass