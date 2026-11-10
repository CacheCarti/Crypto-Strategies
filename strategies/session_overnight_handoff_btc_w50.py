from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BTCSessionImpulse(Strategy):
    METADATA = {
        "name": "BTC Session Impulse",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Aim for 2-4 hour hold
        "warmup_bars": 48, # Need enough bars for 24-hour lookback and EMA
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar = -self.entry_cooldown_bars * 2 # Initialize to allow immediate trading
        self.atr_period = 24 # Use 24-hour ATR for volatility
        self.ema_period = 24 # 24-hour EMA for trend
        self.session_open_hour = 0 # UTC 00:00
        self.session_close_hour = 6 # UTC 06:00
        self.exit_hour = 12 # Exit before US session (UTC 12:00)

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

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
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Exit logic for existing positions
        if ctx.has_position():
            if current_hour == self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_end_exit",
                    "exit_hour": self.exit_hour,
                    "current_price": ctx.bar.close
                })
            # Trailing stop based on ATR from entry
            pos = ctx.position
            entry_price = pos["entry_price"]
            atr = self._atr(highs, lows, closes, self.atr_period)
            if atr is None:
                return None

            # Simple stop: if price moves significantly against us after entry
            if pos["direction"] == "long" and ctx.bar.close < entry_price - 1.5 * atr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "structure_stop_long",
                    "entry_price": entry_price,
                    "current_price": ctx.bar.close,
                    "atr_multiple": 1.5
                })
            if pos["direction"] == "short" and ctx.bar.close > entry_price + 1.5 * atr:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "structure_stop_short",
                    "entry_price": entry_price,
                    "current_price": ctx.bar.close,
                    "atr_multiple": 1.5
                })
            return None # Hold position if no exit condition met

        # Entry cooldown
        if ctx.bar_index - self.last_trade_bar < self.entry_cooldown_bars:
            return None

        # Entry logic: look for impulse during overnight session (0-6 UTC)
        if current_hour == self.session_close_hour: # Trigger at the end of the session
            # Calculate session range
            session_high = max(ctx.highs(self.session_close_hour + 1)[-(self.session_close_hour + 1):])
            session_low = min(ctx.lows(self.session_close_hour + 1)[-(self.session_close_hour + 1):])
            session_open = ctx.opens(self.session_close_hour + 1)[-(self.session_close_hour + 1)] # Open at 00:00 UTC

            # Calculate impulse direction and magnitude
            session_range = session_high - session_low
            if session_range == 0:
                return None

            impulse_dir = None
            if ctx.bar.close > session_open + 0.3 * session_range: # Close significantly higher than open
                impulse_dir = "long"
            elif ctx.bar.close < session_open - 0.3 * session_range: # Close significantly lower than open
                impulse_dir = "short"

            if impulse_dir is None:
                return None

            # Confirmation: current close relative to previous bar and EMA
            ema = self._ema(closes, self.ema_period)
            if ema is None:
                return None

            if impulse_dir == "long" and ctx.bar.close > closes[-2] and ctx.bar.close > ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75, metadata={
                    "reason": "overnight_session_impulse_long",
                    "session_open": session_open,
                    "session_close": ctx.bar.close,
                    "session_range": session_range,
                    "ema": ema,
                    "current_hour": current_hour
                })
            elif impulse_dir == "short" and ctx.bar.close < closes[-2] and ctx.bar.close < ema:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75, metadata={
                    "reason": "overnight_session_impulse_short",
                    "session_open": session_open,
                    "session_close": ctx.bar.close,
                    "session_range": session_range,
                    "ema": ema,
                    "current_hour": current_hour
                })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log or adjust cooldown if needed.
        # For this strategy, a fixed cooldown is used.
        pass