from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityWickRejection(Strategy):
    METADATA = {
        "name": "VolatilityWickRejection",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Up to 4 hours
        "warmup_bars": 50,
        "required_features": [], # No external features used
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12 # Cooldown for 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

        # Parameters for wick rejection
        self.min_wick_body_ratio = 1.5 # Wick must be at least 1.5x the body length
        self.min_volume_increase_factor = 1.5 # Volume must be 1.5x average
        self.volume_avg_period = 20
        self.min_rejection_bar_size_factor = 1.5 # Rejection bar close must be far from open
        self.min_total_bar_range_factor = 1.5 # Bar range must be large relative to recent average

        # ATR for volatility filtering and dynamic thresholds
        self.atr_period = 14
        self.min_atr_multiple = 1.0 # Only trade when current bar range is at least 1.0 * ATR
        self.max_atr_multiple_for_fade = 3.0 # Don't fade extreme moves

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1) # Need one extra for ATR calculation
        opens = ctx.opens(self.METADATA["warmup_bars"] + 1)
        highs = ctx.highs(self.METADATA["warmup_bars"] + 1)
        lows = ctx.lows(self.METADATA["warmup_bars"] + 1)
        volumes = ctx.volumes(self.METADATA["warmup_bars"] + 1)

        if len(closes) < self.METADATA["warmup_bars"] + 1:
            return None

        current_bar = ctx.bar
        prev_bar = {
            "open": opens[-2], "high": highs[-2], "low": lows[-2], "close": closes[-2], "volume": volumes[-2]
        }

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filter: only trade in 'normal' or 'volatile' regimes, avoid 'crisis'
        if ctx.regime == "crisis":
            return None

        # Calculate ATR
        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        # Calculate average volume
        avg_volume = self._sma(volumes[:-1], self.volume_avg_period)
        if avg_volume is None:
            return None

        # Check for previous bar's rejection wick
        prev_body_size = abs(prev_bar["close"] - prev_bar["open"])
        prev_total_range = prev_bar["high"] - prev_bar["low"]

        # Avoid division by zero
        if prev_total_range == 0:
            return None

        # Dynamic range check: ensure previous bar had significant movement
        if prev_total_range < self.min_atr_multiple * current_atr:
            return None
        if prev_total_range > self.max_atr_multiple_for_fade * current_atr:
            # Extreme moves are hard to fade, avoid them
            return None

        # Volume confirmation for the rejection bar
        if prev_bar["volume"] < avg_volume * self.min_volume_increase_factor:
            return None

        long_signal = False
        short_signal = False
        reason = ""
        metadata = {}

        # Bullish Rejection (Long signal)
        # Large lower wick, small body, high volume on previous bar, confirmed by current bar closing higher
        if prev_bar["close"] > prev_bar["open"]: # Green candle
            lower_wick = prev_bar["open"] - prev_bar["low"]
            if prev_body_size > 0 and lower_wick / prev_body_size >= self.min_wick_body_ratio:
                # Confirmation: current bar closes above previous bar's open
                if current_bar.close > prev_bar["open"] and current_bar.open < prev_bar["close"]: # Current bar opens inside prev body and closes higher
                    long_signal = True
                    reason = "bullish_lower_wick_rejection_confirmation"
                    metadata = {
                        "prev_lower_wick_ratio": lower_wick / prev_body_size,
                        "prev_volume_factor": prev_bar["volume"] / avg_volume,
                        "prev_bar_range_atr_multiple": prev_total_range / current_atr,
                        "current_close": current_bar.close,
                        "prev_open": prev_bar["open"]
                    }
        else: # Red candle
            lower_wick = prev_bar["close"] - prev_bar["low"]
            if prev_body_size > 0 and lower_wick / prev_body_size >= self.min_wick_body_ratio:
                # Confirmation: current bar closes above previous bar's open
                if current_bar.close > prev_bar["open"] and current_bar.open < prev_bar["close"]: # Current bar opens inside prev body and closes higher
                    long_signal = True
                    reason = "bullish_lower_wick_rejection_confirmation"
                    metadata = {
                        "prev_lower_wick_ratio": lower_wick / prev_body_size,
                        "prev_volume_factor": prev_bar["volume"] / avg_volume,
                        "prev_bar_range_atr_multiple": prev_total_range / current_atr,
                        "current_close": current_bar.close,
                        "prev_open": prev_bar["open"]
                    }


        # Bearish Rejection (Short signal)
        # Large upper wick, small body, high volume on previous bar, confirmed by current bar closing lower
        if prev_bar["close"] < prev_bar["open"]: # Red candle
            upper_wick = prev_bar["high"] - prev_bar["open"]
            if prev_body_size > 0 and upper_wick / prev_body_size >= self.min_wick_body_ratio:
                # Confirmation: current bar closes below previous bar's open
                if current_bar.close < prev_bar["open"] and current_bar.open > prev_bar["close"]: # Current bar opens inside prev body and closes lower
                    short_signal = True
                    reason = "bearish_upper_wick_rejection_confirmation"
                    metadata = {
                        "prev_upper_wick_ratio": upper_wick / prev_body_size,
                        "prev_volume_factor": prev_bar["volume"] / avg_volume,
                        "prev_bar_range_atr_multiple": prev_total_range / current_atr,
                        "current_close": current_bar.close,
                        "prev_open": prev_bar["open"]
                    }
        else: # Green candle
            upper_wick = prev_bar["high"] - prev_bar["close"]
            if prev_body_size > 0 and upper_wick / prev_body_size >= self.min_wick_body_ratio:
                # Confirmation: current bar closes below previous bar's open
                if current_bar.close < prev_bar["open"] and current_bar.open > prev_bar["close"]: # Current bar opens inside prev body and closes lower
                    short_signal = True
                    reason = "bearish_upper_wick_rejection_confirmation"
                    metadata = {
                        "prev_upper_wick_ratio": upper_wick / prev_body_size,
                        "prev_volume_factor": prev_bar["volume"] / avg_volume,
                        "prev_bar_range_atr_multiple": prev_total_range / current_atr,
                        "current_close": current_bar.close,
                        "prev_open": prev_bar["open"]
                    }


        if long_signal and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("long", confidence=0.7, stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              horizon_seconds=self.METADATA["declared_hold_seconds"],
                              metadata=metadata | {"reason": reason})

        if short_signal and not ctx.has_position():
            self.last_trade_bar = ctx.bar_index
            return ctx.signal("short", confidence=0.7, stop_loss_bps=self.METADATA["declared_sl_bps"],
                              take_profit_bps=self.METADATA["declared_tp_bps"],
                              horizon_seconds=self.METADATA["declared_hold_seconds"],
                              metadata=metadata | {"reason": reason})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to ensure cooldown
        self.last_trade_bar = ctx.bar_index
        pass