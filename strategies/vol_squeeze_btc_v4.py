from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilitySqueezeBreakout(Strategy):
    METADATA = {
        "name": "Volatility Squeeze Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for 6-hour hold on 1h bars
        "warmup_bars": 60, # Need enough bars for 20-period ATR and SMA
        "required_features": [], # No external features used for this strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 20
        self.sma_period = 20
        self.squeeze_threshold_factor = 0.8 # ATR must be this factor below its SMA
        self.breakout_threshold_factor = 1.5 # Current bar range must be this factor above recent average range
        self.cooldown_bars = 12 # Wait 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars - 1 # Initialize to allow immediate trade

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
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        current_atr = self._atr(highs, lows, closes, self.atr_period)
        if current_atr is None:
            return None

        atr_sma = self._sma([self._atr(highs[:i+1], lows[:i+1], closes[:i+1], self.atr_period) for i in range(self.atr_period -1, len(closes))], self.sma_period)
        if atr_sma is None:
            return None

        # Volatility squeeze detection
        is_squeezed = current_atr < atr_sma * self.squeeze_threshold_factor

        # Check for previous squeeze (to ensure we're exiting one)
        # We need to look back a bit to see if there was a squeeze before the current bar
        # For simplicity, we'll check the previous bar's condition
        prev_closes = ctx.closes(self.warmup_bars + 1)
        prev_highs = ctx.highs(self.warmup_bars + 1)
        prev_lows = ctx.lows(self.warmup_bars + 1)

        prev_atr = self._atr(prev_highs[:-1], prev_lows[:-1], prev_closes[:-1], self.atr_period)
        prev_atr_sma = self._sma([self._atr(prev_highs[:i+1], prev_lows[:i+1], prev_closes[:i+1], self.atr_period) for i in range(self.atr_period -1, len(prev_closes)-1)], self.sma_period)

        was_squeezed_prev_bar = False
        if prev_atr is not None and prev_atr_sma is not None:
            was_squeezed_prev_bar = prev_atr < prev_atr_sma * self.squeeze_threshold_factor

        # Market regime filter: only trade in 'normal' or 'volatile' regimes
        if ctx.market.get("regime", "NORMAL") not in ["NORMAL", "HIGH_VOL"]:
             return None

        # Entry logic: breakout after a squeeze
        if was_squeezed_prev_bar and not is_squeezed:
            # Look for a strong breakout candle
            current_range = ctx.bar.high - ctx.bar.low
            # Average range over the squeeze period
            avg_recent_range = sum([h - l for h, l in zip(highs[-self.atr_period:], lows[-self.atr_period:])]) / self.atr_period

            if current_range > avg_recent_range * self.breakout_threshold_factor:
                # Determine direction based on candle close relative to open
                if ctx.bar.close > ctx.bar.open: # Bullish breakout
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("long", confidence=0.75,
                                          metadata={
                                              "reason": "volatility_squeeze_breakout_long",
                                              "current_atr": current_atr,
                                              "atr_sma": atr_sma,
                                              "current_range": current_range,
                                              "avg_recent_range": avg_recent_range,
                                              "regime": ctx.market.get("regime", "NORMAL")
                                          })
                elif ctx.bar.close < ctx.bar.open: # Bearish breakout
                    if not ctx.has_position():
                        self.last_trade_bar = ctx.bar_index
                        return ctx.signal("short", confidence=0.75,
                                          metadata={
                                              "reason": "volatility_squeeze_breakout_short",
                                              "current_atr": current_atr,
                                              "atr_sma": atr_sma,
                                              "current_range": current_range,
                                              "avg_recent_range": avg_recent_range,
                                              "regime": ctx.market.get("regime", "NORMAL")
                                          })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic for this strategy on close, just reset cooldown.
        # Cooldown is handled by `self.last_trade_bar` update in `on_bar`.
        pass