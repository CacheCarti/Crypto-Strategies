from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionFade(Strategy):
    METADATA = {
        "name": "Asia Session Fade",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Max hold 4 hours, but exit by 8 UTC
        "warmup_bars": 50, # For EMA50
        "required_features": [], # No specific features required for this one
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 50
        self.session_start_hour = 0  # UTC 00:00
        self.session_end_hour = 8    # UTC 08:00
        self.min_stretch_pct = 0.5   # Minimum percentage move to consider "stretched"
        self.cooldown_bars = 5       # Bars to wait after a trade
        self.last_trade_bar = -self.cooldown_bars
        self.session_high = -1.0
        self.session_low = float('inf')
        self.session_start_price = -1.0
        self.session_midpoint = -1.0
        self.session_active = False
        self.signal_sent_today = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period + 1)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Reset session data at the start of a new session (0 UTC)
        if current_hour == self.session_start_hour and not self.session_active:
            self.session_active = True
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_price = ctx.bar.open
            self.session_midpoint = self.session_start_price # Initialize midpoint
            self.signal_sent_today = False
        elif current_hour != self.session_start_hour and self.session_active:
            # Update session high/low
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            # Recalculate session midpoint dynamically
            self.session_midpoint = (self.session_high + self.session_low) / 2.0

        # End session logic
        if current_hour >= self.session_end_hour and self.session_active:
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour, "session_end": self.session_end_hour})
            self.session_active = False
            self.signal_sent_today = False # Allow new signal next day

        if not self.session_active:
            return None # Only trade during the active session

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Calculate EMA
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Check for crisis regime or strong trend
        # We don't want to fade during crisis or very strong trends
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"] or \
           ctx.market.get("trend_regime_confidence", 0.0) > 0.6: # High confidence trend
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "regime_change_exit", "regime": ctx.market.get("regime"), "trend_conf": ctx.market.get("trend_regime_confidence")})
            return None

        # Calculate session stretch
        session_range = self.session_high - self.session_low
        if session_range == 0:
            return None
        
        stretch_pct_up = (current_close - self.session_start_price) / self.session_start_price * 100
        stretch_pct_down = (self.session_start_price - current_close) / self.session_start_price * 100

        signal = None
        if not ctx.has_position() and not self.signal_sent_today:
            # Check for stretched up and close above midpoint - fade short
            if stretch_pct_up > self.min_stretch_pct and current_close > self.session_midpoint * 1.001: # 0.1% buffer
                # Ensure price is above EMA to confirm "stretched up"
                if current_close > ema:
                    signal = ctx.signal(
                        "short",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_session_fade_short",
                            "session_start_price": self.session_start_price,
                            "current_close": current_close,
                            "session_midpoint": self.session_midpoint,
                            "stretch_pct": stretch_pct_up,
                            "ema": ema,
                            "session_high": self.session_high,
                            "session_low": self.session_low
                        }
                    )
                    self.signal_sent_today = True

            # Check for stretched down and close below midpoint - fade long
            elif stretch_pct_down > self.min_stretch_pct and current_close < self.session_midpoint * 0.999: # 0.1% buffer
                # Ensure price is below EMA to confirm "stretched down"
                if current_close < ema:
                    signal = ctx.signal(
                        "long",
                        confidence=0.7,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_session_fade_long",
                            "session_start_price": self.session_start_price,
                            "current_close": current_close,
                            "session_midpoint": self.session_midpoint,
                            "stretch_pct": stretch_pct_down,
                            "ema": ema,
                            "session_high": self.session_high,
                            "session_low": self.session_low
                        }
                    )
                    self.signal_sent_today = True

        if signal:
            self.last_trade_bar = ctx.bar_index
        return signal

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not strictly necessary for this strategy, but good practice to include.
        # Could use this for adaptive sizing or logging.
        pass