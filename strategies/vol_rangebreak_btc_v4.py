from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityRangeBreak(Strategy):
    METADATA = {
        "name": "VolatilityRangeBreak_BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2-hour avg hold
        "warmup_bars": 60,
        "required_features": [], # No specific features required, uses OHLCV and market context
    }

    def initialize(self, ctx: BarContext) -> None:
        self.range_period = 20 # Period to define the range
        self.breakout_threshold_multiplier = 0.5 # How far beyond the range for a "failed" break
        self.reversion_period = 5 # How many bars for price to return inside the range
        self.cooldown_bars = 12 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Volatility filter parameters
        self.vol_period = 20
        self.min_atr_multiplier = 0.0005 # Minimum ATR percentage of price for trading
        self.max_atr_multiplier = 0.005  # Maximum ATR percentage of price for trading

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Volatility filter
        current_atr = self._atr(highs, lows, closes, self.vol_period)
        if current_atr is None:
            return None

        # Convert ATR to a percentage of current price
        current_price = closes[-1]
        atr_pct = (current_atr / current_price)
        
        # Only trade in a suitable volatility regime (not too low, not too high)
        if not (self.min_atr_multiplier < atr_pct < self.max_atr_multiplier):
            return None
            
        # Regime filter: Only trade in NORMAL or HIGH_VOL regime
        if ctx.market.get("regime", "NORMAL") not in ["NORMAL", "HIGH_VOL"]:
            return None

        # Define the range
        range_high = max(highs[-self.range_period-self.reversion_period:-self.reversion_period])
        range_low = min(lows[-self.range_period-self.reversion_period:-self.reversion_period])
        
        # Check for a failed breakout
        # A failed long breakout: price broke above range_high, but then came back inside
        # A failed short breakout: price broke below range_low, but then came back inside

        # Check if a clear breakout occurred in the recent past
        # Look for a bar that closed significantly above/below the established range
        
        # Failed long breakout setup
        failed_long_breakout = False
        if closes[-self.reversion_period-1] > range_high * (1 + self.breakout_threshold_multiplier / 10000): # Closed above threshold
            # Check if price then returned inside the range
            if closes[-1] < range_high:
                failed_long_breakout = True

        # Failed short breakout setup
        failed_short_breakout = False
        if closes[-self.reversion_period-1] < range_low * (1 - self.breakout_threshold_multiplier / 10000): # Closed below threshold
            # Check if price then returned inside the range
            if closes[-1] > range_low:
                failed_short_breakout = True

        # If a position is open, check if it's time to close
        if ctx.has_position():
            # If the current price has moved significantly back into the range after a failed breakout,
            # or if the market regime changes drastically, consider exiting.
            # For simplicity, we rely on SL/TP and horizon, but can add explicit exit conditions here.
            # Example: if ctx.market.get("regime") == "CRISIS": return ctx.signal("flat", confidence=0.8, metadata={"reason": "crisis_regime_exit"})
            return None # Let the engine manage SL/TP

        # Entry logic
        if failed_long_breakout:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_long_breakout_reversion",
                    "current_price": closes[-1],
                    "range_high": range_high,
                    "range_low": range_low,
                    "atr_pct": atr_pct,
                    "regime": ctx.market.get("regime", "NORMAL"),
                }
            )

        if failed_short_breakout:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "failed_short_breakout_reversion",
                    "current_price": closes[-1],
                    "range_high": range_high,
                    "range_low": range_low,
                    "atr_pct": atr_pct,
                    "regime": ctx.market.get("regime", "NORMAL"),
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optionally log or adapt strategy based on position outcome
        # self.last_trade_bar is already updated on signal generation.
        pass