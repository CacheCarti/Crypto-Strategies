from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEuropeSessionImpulse(Strategy):
    METADATA = {
        "name": "EthEuropeSessionImpulse",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 5,  # Up to 5 hours max
        "warmup_bars": 30,
        "required_features": [], # No external features used here
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -999
        self.cooldown_bars = 5
        self.session_entry_hour_start = 7  # UTC 7 AM
        self.session_entry_hour_end = 10   # UTC 10 AM (allow entry up to 9:59)
        self.session_exit_hour = 12        # UTC 12 PM
        self.impulse_period = 3            # Bars to measure impulse
        self.impulse_threshold_pct = 0.3   # Minimum % move to consider as impulse
        self.min_volatility_threshold = 0.0005 # Min ATR/close to trade (0.05%)
        self.max_volatility_threshold = 0.005 # Max ATR/close to trade (0.5%)
        self.ema_period = 20 # For trend filtering

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        
        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Calculate EMA for trend filtering
        ema_20 = self._ema(closes, self.ema_period)
        if ema_20 is None:
            return None

        # Calculate ATR for volatility filtering
        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val is None or closes[-1] == 0:
            return None
        atr_pct = atr_val / closes[-1]

        # Exit logic: Close position by session end or if volatility changes
        if ctx.has_position():
            if current_hour >= self.session_exit_hour and ctx.bar.timestamp.minute == 0:
                reason = "session_time_exit"
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.8, metadata={"reason": reason, "exit_hour": current_hour, "price": current_price})
            
            # Additional volatility check for exiting
            if atr_pct < self.min_volatility_threshold / 2 or atr_pct > self.max_volatility_threshold * 1.5:
                reason = "volatility_regime_exit"
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.6, metadata={"reason": reason, "atr_pct": atr_pct, "price": current_price})

            return None # Hold position if no exit condition met

        # Entry logic: Only consider entries within the defined session window
        if not (self.session_entry_hour_start <= current_hour < self.session_entry_hour_end):
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Regime filter: Stand down in extreme volatility
        if not (self.min_volatility_threshold <= atr_pct <= self.max_volatility_threshold):
            return None
        
        # Trend filter: Only trade with the trend
        # For simplicity, we'll only look for long signals in an uptrend and short in a downtrend.
        # This prevents whipsaws against the larger trend.
        is_uptrend = current_price > ema_20
        is_downtrend = current_price < ema_20

        # Measure session impulse
        if len(closes) < self.impulse_period + 1:
            return None

        # Check if we are at the start of the entry window (e.g., 7 AM UTC)
        # We need to ensure we only measure impulse once per session at its beginning
        # To avoid re-triggering if the impulse persists.
        # This specific logic will allow a signal only if the previous bar was NOT in the entry window
        # or if it's the first bar of the entry window hour.
        if current_hour == self.session_entry_hour_start and ctx.bar.timestamp.minute == 0:
            # Calculate impulse from the start of the session to current
            impulse_start_price = closes[-self.impulse_period - 1] # Price N bars ago
            impulse_end_price = closes[-1]
            price_change_pct = (impulse_end_price - impulse_start_price) / impulse_start_price * 100

            if price_change_pct > self.impulse_threshold_pct and is_uptrend:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "europe_session_long_impulse",
                        "entry_hour": current_hour,
                        "impulse_pct": price_change_pct,
                        "price": current_price,
                        "ema_20": ema_20,
                        "atr_pct": atr_pct
                    }
                )
            elif price_change_pct < -self.impulse_threshold_pct and is_downtrend:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "europe_session_short_impulse",
                        "entry_hour": current_hour,
                        "impulse_pct": price_change_pct,
                        "price": current_price,
                        "ema_20": ema_20,
                        "atr_pct": atr_pct
                    }
                )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log position details or adapt future behavior if needed
        # For this strategy, simply updating last_trade_bar is sufficient
        pass