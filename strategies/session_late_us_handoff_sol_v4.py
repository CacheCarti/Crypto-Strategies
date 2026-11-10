from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolUsSessionHandoff(Strategy):
    METADATA = {
        "name": "SOL US Session Handoff",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4-hour hold
        "warmup_bars": 50,
        "required_features": [], # No specific features required beyond OHLCV
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_period = 20
        self.short_period = 5
        self.atr_period = 14
        self.min_session_impulse = 0.005 # 0.5% impulse
        self.entry_hour_start = 18 # UTC 18:00 (late US session)
        self.entry_hour_end = 23 # UTC 23:00
        self.exit_hour_boundary = 0 # UTC 00:00 (start of next day)
        self.cooldown_bars = 4 # Cooldown after a trade
        self.last_trade_bar_index: Optional[int] = None

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_period + self.atr_period)
        highs = ctx.highs(self.long_period + self.atr_period)
        lows = ctx.lows(self.long_period + self.atr_period)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Regime filter: Stand down in crisis or extreme volatility
        if ctx.market.get("regime") in ["CRISIS", "MELTDOWN"]:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_crisis_exit"})
            return None
        
        # Cooldown logic
        if self.last_trade_bar_index is not None and ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Exit logic: Close position at the boundary hour
        if ctx.has_position() and current_hour == self.exit_hour_boundary:
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_boundary_exit", "exit_hour": current_hour})

        # Entry logic: Only within the late US session window (18-23 UTC)
        if self.entry_hour_start <= current_hour <= self.entry_hour_end:
            if ctx.has_position():
                return None # Already in a position

            # Calculate session impulse
            # We need the close from the previous hour that started the window
            # For 18:00 bar, we need 17:00 close. For 19:00 bar, we need 18:00 close.
            # Use the close price from the beginning of the current hour block for impulse calculation
            
            # Find the close price at the start of the current entry window (e.g., 18:00 for the 18:00-23:00 window)
            # This is tricky with `closes` returning a slice. We need to find the close at the *start* of the current session entry window.
            # For simplicity, let's define the "session impulse" as the change from the open of the current bar to its close.
            # Or, better, from a few bars back if we are looking for a sustained impulse.
            
            # Let's define impulse based on the last 3 bars within the session window
            if len(closes) < 3:
                return None
            
            # Use SMA crossover for direction and strength
            sma_short = self._sma(closes, self.short_period)
            sma_long = self._sma(closes, self.long_period)

            if sma_short is None or sma_long is None:
                return None
            
            current_close = ctx.bar.close
            prev_close = closes[-2] # Close of the previous bar

            # Volatility filter using ATR
            current_atr = self._atr(highs, lows, closes, self.atr_period)
            if current_atr is None or current_atr == 0:
                return None
            
            # Entry conditions
            signal_direction: Optional[str] = None
            reason: str = ""
            confidence = 0.0

            # Long entry: Short SMA crosses above Long SMA, and price is above both, and positive impulse
            if sma_short > sma_long and prev_close < current_close and current_close > sma_long:
                impulse_pct = (current_close - prev_close) / prev_close
                if impulse_pct > self.min_session_impulse:
                    signal_direction = "long"
                    reason = "session_long_impulse_sma_cross"
                    confidence = 0.75 # High confidence for strong impulse and trend
            
            # Short entry: Short SMA crosses below Long SMA, and price is below both, and negative impulse
            elif sma_short < sma_long and prev_close > current_close and current_close < sma_long:
                impulse_pct = (prev_close - current_close) / prev_close
                if impulse_pct > self.min_session_impulse:
                    signal_direction = "short"
                    reason = "session_short_impulse_sma_cross"
                    confidence = 0.75 # High confidence for strong impulse and trend

            if signal_direction:
                # Gating by market trend regime
                trend_regime = ctx.market.get("trend_regime", "neutral")
                if (signal_direction == "long" and trend_regime == "bear") or \
                   (signal_direction == "short" and trend_regime == "bull"):
                    return None # Stand down if trend is against our signal

                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_hour": current_hour,
                        "current_close": current_close,
                        "sma_short": sma_short,
                        "sma_long": sma_long,
                        "atr": current_atr,
                        "trend_regime": trend_regime
                    }
                )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No specific adaptive logic for now, just reset cooldown
        self.last_trade_bar_index = ctx.bar_index