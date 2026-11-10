from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityQuietTrendSOL(Strategy):
    METADATA = {
        "name": "VolatilityQuietTrendSOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 40
        self.short_period = 10
        self.atr_period = 20
        self.last_signal_bar = -self.long_period # Allow signal immediately after warmup
        self.cooldown_bars = 10 # Cooldown for 10 bars after any signal

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if len(trs) < period: # Not enough TRs for the full period yet
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + 1) # Need one more for ATR prev close
        highs = ctx.highs(self.long_period + 1)
        lows = ctx.lows(self.long_period + 1)
        volumes = ctx.volumes(self.long_period)

        if len(closes) < self.long_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index <= self.last_signal_bar + self.cooldown_bars:
            return None

        current_price = ctx.bar.close

        long_sma = self._sma(closes[:-1], self.long_period) # SMA of previous bars
        short_sma = self._sma(closes[:-1], self.short_period) # SMA of previous bars
        current_atr = self._atr(highs, lows, closes, self.atr_period)

        if long_sma is None or short_sma is None or current_atr is None:
            return None

        # Volatility filter: Only trade in low volatility regimes
        # We define low volatility as ATR being below its recent average
        # Using a simple SMA of ATR for this.
        atr_history = []
        for i in range(self.atr_period, len(closes)):
            atr_val = self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period)
            if atr_val is not None:
                atr_history.append(atr_val)

        if len(atr_history) < self.atr_period: # Need enough ATR values to average them
            return None

        avg_atr = sum(atr_history[-self.atr_period:]) / self.atr_period
        
        # Volatility expansion check: If current ATR is significantly higher than its average, it's volatile
        volatility_threshold = 1.2 # Current ATR > 1.2 * avg_atr indicates expansion
        is_volatile = current_atr > (avg_atr * volatility_threshold)

        # Trend filter: Only enter if short_sma > long_sma (bullish) or short_sma < long_sma (bearish)
        # Entry requires price to be confirmed on the close relative to SMAs
        
        # Determine trend direction from SMAs of PREVIOUS bar (to avoid look-ahead bias if using current close)
        prev_close = closes[-2]
        prev_long_sma = self._sma(closes[:-2], self.long_period)
        prev_short_sma = self._sma(closes[:-2], self.short_period)

        if prev_long_sma is None or prev_short_sma is None:
            return None

        # Event boundary: Look for a clean cross of the short SMA over/under the long SMA
        # This ensures we only enter on a *transition* into a trend, not during an ongoing trend.
        bullish_cross_event = prev_short_sma <= prev_long_sma and short_sma > long_sma
        bearish_cross_event = prev_short_sma >= prev_long_sma and short_sma < long_sma

        signal_direction = None
        signal_confidence = 0.0
        signal_reason = ""
        signal_metadata = {
            "current_price": current_price,
            "short_sma": short_sma,
            "long_sma": long_sma,
            "current_atr": current_atr,
            "avg_atr_recent": avg_atr,
        }

        # Long Entry Conditions
        if not ctx.has_position() and not is_volatile and bullish_cross_event:
            # Confirm current close is above both SMAs for strong entry confirmation
            if current_price > short_sma and current_price > long_sma:
                signal_direction = "long"
                signal_confidence = 0.75 # High confidence for confirmed trend in low vol
                signal_reason = "vol_quiet_bull_trend_entry"
                self.last_signal_bar = ctx.bar_index

        # Short Entry Conditions
        elif not ctx.has_position() and not is_volatile and bearish_cross_event:
            # Confirm current close is below both SMAs for strong entry confirmation
            if current_price < short_sma and current_price < long_sma:
                signal_direction = "short"
                signal_confidence = 0.75 # High confidence for confirmed trend in low vol
                signal_reason = "vol_quiet_bear_trend_entry"
                self.last_signal_bar = ctx.bar_index

        # Exit Conditions (Volatility Expansion)
        # If we have a position and volatility expands, exit.
        if ctx.has_position() and is_volatile:
            signal_direction = "flat"
            signal_confidence = 0.8 # Strong reason to exit
            signal_reason = "volatility_expansion_exit"
            self.last_signal_bar = ctx.bar_index
            
        # If we have a position and the trend reverses (SMAs cross against position)
        if ctx.has_position():
            if ctx.position_direction() == "long" and bearish_cross_event:
                signal_direction = "flat"
                signal_confidence = 0.6
                signal_reason = "trend_reversal_exit_long"
                self.last_signal_bar = ctx.bar_index
            elif ctx.position_direction() == "short" and bullish_cross_event:
                signal_direction = "flat"
                signal_confidence = 0.6
                signal_reason = "trend_reversal_exit_short"
                self.last_signal_bar = ctx.bar_index

        if signal_direction:
            return ctx.signal(
                signal_direction,
                confidence=signal_confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": signal_reason,
                    **signal_metadata
                }
            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown after any position close
        self.last_signal_bar = ctx.bar_index
        pass