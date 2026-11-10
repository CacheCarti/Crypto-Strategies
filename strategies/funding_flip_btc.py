from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingFlip(Strategy):
    METADATA = {
        "name": "BTC Funding Flip",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 24 # Use 24 bars (24 hours) for funding rate trend
        self.price_confirm_period = 3 # Bars to check price confirmation after funding flip
        self.cooldown_bars = 10 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # For price confirmation - simple EMA
        self.ema_period = 10
        self.ema_values = []

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        # Update EMA values
        self.ema_values.append(ctx.bar.close)
        current_ema = self._ema(self.ema_values, self.ema_period)
        if current_ema is None:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        previous_funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0, offset=-i) for i in range(1, self.funding_period + 1)]

        # Ensure we have enough data for previous funding rates
        if len(previous_funding_rates) < self.funding_period:
            return None

        # Calculate average funding rate over the period
        avg_funding_rate = sum(previous_funding_rates) / self.funding_period

        # Funding Flip Logic
        long_condition = False
        short_condition = False

        # Long: Funding was significantly negative and is now positive/less negative
        # Short: Funding was significantly positive and is now negative/less positive

        # Check for a flip from negative to positive (or less negative)
        if avg_funding_rate < -0.00005 and current_funding_rate > -0.00001: # e.g. from -0.005% to near 0 or positive
            long_condition = True

        # Check for a flip from positive to negative (or less positive)
        if avg_funding_rate > 0.00005 and current_funding_rate < 0.00001: # e.g. from +0.005% to near 0 or negative
            short_condition = True

        # Price Confirmation (simple EMA cross/position)
        # For long: current price above EMA
        # For short: current price below EMA
        price_confirm_long = ctx.bar.close > current_ema
        price_confirm_short = ctx.bar.close < current_ema

        # Entry logic
        if not ctx.has_position():
            if long_condition and price_confirm_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_flip_negative_to_positive_and_ema_confirm",
                        "current_funding_rate": current_funding_rate,
                        "avg_funding_rate": avg_funding_rate,
                        "current_close": ctx.bar.close,
                        "ema": current_ema,
                    }
                )
            elif short_condition and price_confirm_short:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "funding_flip_positive_to_negative_and_ema_confirm",
                        "current_funding_rate": current_funding_rate,
                        "avg_funding_rate": avg_funding_rate,
                        "current_close": ctx.bar.close,
                        "ema": current_ema,
                    }
                )
        elif ctx.has_position():
            # If current position is long and funding turns strongly negative, consider exiting or flipping
            if ctx.position_direction() == "long" and current_funding_rate < -0.0001:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "long_exit_funding_turns_strongly_negative",
                        "current_funding_rate": current_funding_rate,
                        "entry_price": ctx.position["entry_price"],
                        "current_close": ctx.bar.close,
                    }
                )
            # If current position is short and funding turns strongly positive, consider exiting or flipping
            elif ctx.position_direction() == "short" and current_funding_rate > 0.0001:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "short_exit_funding_turns_strongly_positive",
                        "current_funding_rate": current_funding_rate,
                        "entry_price": ctx.position["entry_price"],
                        "current_close": ctx.bar.close,
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar to apply cooldown after any position closes
        self.last_trade_bar = ctx.bar_index
        # You could also log details or adapt confidence based on performance
        pass