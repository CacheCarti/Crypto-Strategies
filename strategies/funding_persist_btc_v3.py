from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingPersistence(Strategy):
    METADATA = {
        "name": "BTC Funding Persistence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours average hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 6 # Check funding over last 6 bars (6 hours)
        self.funding_threshold = 0.00015 # 0.015% per 8h funding rate, extreme level
        self.persistence_count = 3 # How many consecutive bars funding must be extreme
        self.ema_period = 20 # EMA for price confirmation
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_signal_bar_index = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        funding_rates = [ctx.features.get("funding_rate_btcusdt", 0.0) for _ in range(self.warmup_bars)]

        if len(closes) < self.warmup_bars or len(funding_rates) < self.warmup_bars:
            return None

        if ctx.bar_index < self.last_signal_bar_index + self.cooldown_bars:
            return None # Cooldown active

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)

        if ema is None:
            return None

        # Check for persistent extreme funding
        long_signal = False
        short_signal = False
        
        # Funding persistence for long signal (extreme negative funding)
        if len(funding_rates) >= self.funding_period:
            recent_funding = funding_rates[-self.funding_period:]
            negative_funding_count = sum(1 for fr in recent_funding if fr < -self.funding_threshold)
            
            if negative_funding_count >= self.persistence_count:
                # Check if it just transitioned into this state to avoid repeated signals
                # Look at the previous window's funding state
                prev_funding_window = funding_rates[-self.funding_period-1:-1]
                prev_negative_funding_count = sum(1 for fr in prev_funding_window if fr < -self.funding_threshold)
                
                if prev_negative_funding_count < self.persistence_count: # Only signal on transition
                    if current_price > ema: # Price confirmation: above EMA
                        long_signal = True

        # Funding persistence for short signal (extreme positive funding)
        if len(funding_rates) >= self.funding_period:
            recent_funding = funding_rates[-self.funding_period:]
            positive_funding_count = sum(1 for fr in recent_funding if fr > self.funding_threshold)
            
            if positive_funding_count >= self.persistence_count:
                # Check if it just transitioned into this state
                prev_funding_window = funding_rates[-self.funding_period-1:-1]
                prev_positive_funding_count = sum(1 for fr in prev_funding_window if fr > self.funding_threshold)

                if prev_positive_funding_count < self.persistence_count: # Only signal on transition
                    if current_price < ema: # Price confirmation: below EMA
                        short_signal = True

        if ctx.has_position():
            if long_signal and ctx.position_direction() == "short":
                # Flip from short to long
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("long", confidence=0.8, metadata={
                    "reason": "funding_persistence_flip_short_to_long",
                    "funding_rate_last": funding_rates[-1],
                    "funding_persistence_count": negative_funding_count,
                    "price": current_price,
                    "ema_period": self.ema_period,
                    "ema_value": ema,
                })
            elif short_signal and ctx.position_direction() == "long":
                # Flip from long to short
                self.last_signal_bar_index = ctx.bar_index
                return ctx.signal("short", confidence=0.8, metadata={
                    "reason": "funding_persistence_flip_long_to_short",
                    "funding_rate_last": funding_rates[-1],
                    "funding_persistence_count": positive_funding_count,
                    "price": current_price,
                    "ema_period": self.ema_period,
                    "ema_value": ema,
                })
            return None # Hold current position

        if long_signal:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal("long", confidence=0.7, metadata={
                "reason": "funding_persistence_long_entry",
                "funding_rate_last": funding_rates[-1],
                "funding_persistence_count": negative_funding_count,
                "price": current_price,
                "ema_period": self.ema_period,
                "ema_value": ema,
            })
        elif short_signal:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal("short", confidence=0.7, metadata={
                "reason": "funding_persistence_short_entry",
                "funding_rate_last": funding_rates[-1],
                "funding_persistence_count": positive_funding_count,
                "price": current_price,
                "ema_period": self.ema_period,
                "ema_value": ema,
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy currently
        pass