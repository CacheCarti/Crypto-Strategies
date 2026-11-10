from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionAsiaHandoffEth(Strategy):
    METADATA = {
        "name": "Session Asia Handoff ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold through next session handoff
        "warmup_bars": 50,
        "required_features": [], # No specific features needed, only OHLCV and time
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.rsi_period = 14
        self.last_trade_bar = -self.warmup_bars # Initialize to allow trading after warmup
        self.cooldown_bars = 5 # Wait 5 bars after a trade before new entry
        self.last_asia_session_direction = None # To detect session direction change
        self.session_traded_today = False # To ensure only one trade per day/session
        self.last_bar_day = -1 # To detect new day for session_traded_today reset

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Prevent division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset daily trade flag at the start of a new day
        if current_day != self.last_bar_day:
            self.session_traded_today = False
            self.last_bar_day = current_day
            self.last_asia_session_direction = None # Reset session direction for new day

        # Calculate indicators
        ema = self._ema(closes, self.ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema is None or rsi is None:
            return None

        # Determine Asia session (UTC 0-8) direction
        # We need at least self.ema_period bars within the Asia session to determine direction
        # We'll use the EMA trend within the session
        if 0 <= current_hour < 8: # Asia session
            # If we just entered the Asia session (e.g., hour 0), reset direction
            if current_hour == 0 and self.last_asia_session_direction is not None:
                self.last_asia_session_direction = None

            # Calculate EMA for the current Asia session bars
            asia_closes = []
            for i in range(len(closes) - 1, -1, -1):
                bar_time = ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0) - (len(closes) - 1 - i) * (ctx.bar.timestamp - ctx.bar.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / len(closes)
                if 0 <= bar_time.hour < 8:
                    asia_closes.insert(0, closes[i])
                else:
                    break

            if len(asia_closes) >= self.ema_period:
                asia_ema_current = self._ema(asia_closes, self.ema_period)
                asia_ema_prev = self._ema(asia_closes[:-1], self.ema_period) # EMA of previous bars in this session

                if asia_ema_current is not None and asia_ema_prev is not None:
                    if asia_ema_current > asia_ema_prev:
                        self.last_asia_session_direction = "bullish"
                    elif asia_ema_current < asia_ema_prev:
                        self.last_asia_session_direction = "bearish"
                    else:
                        self.last_asia_session_direction = "neutral"

        # Trading logic (outside Asia session, for handoff)
        if ctx.has_position():
            # Exit before UTC 8:00 if position is still open
            if current_hour == 7 and ctx.bar.timestamp.minute == 55: # 5 minutes before 8:00 UTC
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_close_time", "current_hour": current_hour, "timestamp": str(ctx.bar.timestamp)})
            return None # Hold position if no exit condition met

        # Cooldown period
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Entry logic: after Asia session, if direction was clear and no trade today
        if not self.session_traded_today and self.last_asia_session_direction is not None and 8 <= current_hour < 10: # Trade in the 8-9 UTC window (after Asia session)
            # Long entry: Bullish Asia session, price above EMA, RSI not overbought
            if self.last_asia_session_direction == "bullish" and closes[-1] > ema and rsi < 70:
                self.session_traded_today = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.75,
                                  metadata={"reason": "asia_session_bullish_handoff",
                                            "asia_session_direction": self.last_asia_session_direction,
                                            "current_hour": current_hour,
                                            "ema": ema, "rsi": rsi, "price": closes[-1]})

            # Short entry: Bearish Asia session, price below EMA, RSI not oversold
            elif self.last_asia_session_direction == "bearish" and closes[-1] < ema and rsi > 30:
                self.session_traded_today = True
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.75,
                                  metadata={"reason": "asia_session_bearish_handoff",
                                            "asia_session_direction": self.last_asia_session_direction,
                                            "current_hour": current_hour,
                                            "ema": ema, "rsi": rsi, "price": closes[-1]})

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details for analysis
        pass