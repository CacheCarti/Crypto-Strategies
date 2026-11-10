from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingPersistenceBTC(Strategy):
    METADATA = {
        "name": "Funding Persistence BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 12 # Check persistence over 12 hours (12 bars)
        self.funding_threshold = 0.0002 # 0.02% per 8 hours as an extreme threshold
        self.ema_period = 20 # For price confirmation
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade

        self.last_signal_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trading

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

        closes = ctx.closes(self.ema_period)
        if len(closes) < self.ema_period:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)

        if ema is None:
            return None

        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.funding_period)]
        # Ensure we have enough historical funding rates
        if len(funding_rates) < self.funding_period:
            return None

        # Check for persistent extreme funding
        long_bias_funding = all(f > self.funding_threshold for f in funding_rates)
        short_bias_funding = all(f < -self.funding_threshold for f in funding_rates)

        signal_direction = None
        confidence = 0.0
        reason = ""
        metadata = {}

        if not ctx.has_position():
            # Extreme positive funding (people are paying a lot to be long) -> potential short
            if long_bias_funding and current_price > ema: # Price confirmation: above EMA suggests current trend is up, ripe for reversal
                signal_direction = "short"
                confidence = 0.75
                reason = "persistent_high_funding_ema_confirm_short"
                metadata = {
                    "funding_rates_last_N_bars": funding_rates,
                    "funding_threshold": self.funding_threshold,
                    "current_price": current_price,
                    "ema_price": ema,
                    "ema_period": self.ema_period,
                }
            # Extreme negative funding (people are paying a lot to be short) -> potential long
            elif short_bias_funding and current_price < ema: # Price confirmation: below EMA suggests current trend is down, ripe for reversal
                signal_direction = "long"
                confidence = 0.75
                reason = "persistent_low_funding_ema_confirm_long"
                metadata = {
                    "funding_rates_last_N_bars": funding_rates,
                    "funding_threshold": self.funding_threshold,
                    "current_price": current_price,
                    "ema_price": ema,
                    "ema_period": self.ema_period,
                }

        if signal_direction:
            self.last_signal_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata=metadata
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already have a cooldown mechanism in on_bar, but could reset state here if needed.
        # For this strategy, no specific state needs to be reset on close.
        pass