from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingFlipConfirmation(Strategy):
    METADATA = {
        "name": "BTC Funding Flip Confirmation",
        "domain": "btc_usdc",
        "declared_sl_bps": 180.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_period = 2 # bars for funding rate change detection
        self.confirmation_period = 5 # bars for price confirmation
        self.cooldown_bars = 10 # bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

        # Store previous funding rate to detect flips
        self.prev_funding_rate = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Ensure we have enough bars for price confirmation
        closes = ctx.closes(self.confirmation_period + 1)
        if len(closes) < self.confirmation_period + 1:
            self.prev_funding_rate = current_funding
            return None

        # Detect funding flip
        funding_flip_long = False
        funding_flip_short = False

        if self.prev_funding_rate is not None:
            # Short signal: Funding flips from positive/neutral to negative
            if self.prev_funding_rate >= 0 and current_funding < 0:
                funding_flip_short = True
            # Long signal: Funding flips from negative/neutral to positive
            elif self.prev_funding_rate <= 0 and current_funding > 0:
                funding_flip_long = True

        self.prev_funding_rate = current_funding

        # Price confirmation logic
        # For long: price moved up after the flip (e.g., current close > prev close, or SMA is rising)
        # For short: price moved down after the flip (e.g., current close < prev close, or SMA is falling)
        
        # Using a short-term SMA to confirm direction
        short_sma_period = self.confirmation_period
        sma_values = [self._sma(ctx.closes(i+1), short_sma_period) for i in range(short_sma_period, len(closes))]
        
        if len(sma_values) < 2:
            return None # Not enough data for SMA comparison

        current_sma = sma_values[-1]
        prev_sma = sma_values[-2]

        price_confirm_long = current_sma > prev_sma
        price_confirm_short = current_sma < prev_sma

        # Check for trade entry conditions
        if not ctx.has_position():
            if funding_flip_long and price_confirm_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_flip_long_price_confirm",
                        "current_funding_rate": current_funding,
                        "prev_funding_rate": self.prev_funding_rate,
                        "current_sma": current_sma,
                        "prev_sma": prev_sma,
                        "close": ctx.bar.close
                    }
                )
            elif funding_flip_short and price_confirm_short:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    metadata={
                        "reason": "funding_flip_short_price_confirm",
                        "current_funding_rate": current_funding,
                        "prev_funding_rate": self.prev_funding_rate,
                        "current_sma": current_sma,
                        "prev_sma": prev_sma,
                        "close": ctx.bar.close
                    }
                )
        elif ctx.has_position():
            # If funding flips against current position, consider exiting or flipping
            if ctx.position_direction() == "long" and funding_flip_short:
                self.last_trade_bar = ctx.bar_index # Consider this a new trade for cooldown purposes
                return ctx.signal(
                    "short", # Flip to short
                    confidence=0.8,
                    metadata={
                        "reason": "funding_flip_against_long_flip_short",
                        "current_funding_rate": current_funding,
                        "prev_funding_rate": self.prev_funding_rate,
                        "close": ctx.bar.close
                    }
                )
            elif ctx.position_direction() == "short" and funding_flip_long:
                self.last_trade_bar = ctx.bar_index # Consider this a new trade for cooldown purposes
                return ctx.signal(
                    "long", # Flip to long
                    confidence=0.8,
                    metadata={
                        "reason": "funding_flip_against_short_flip_long",
                        "current_funding_rate": current_funding,
                        "prev_funding_rate": self.prev_funding_rate,
                        "close": ctx.bar.close
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic for now, just reset cooldown
        self.last_trade_bar = ctx.bar_index
        pass