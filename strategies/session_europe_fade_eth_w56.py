from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuroSessionFade(Strategy):
    METADATA = {
        "name": "EuroSessionFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold until 12 UTC, roughly 2 hours from 10 UTC entry
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "eth_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_hour_start = 7 # UTC
        self.entry_hour_end = 10 # UTC, last hour to consider entry
        self.exit_hour = 12 # UTC, hard exit time
        self.session_bar_count = 0
        self.session_high = -math.inf
        self.session_low = math.inf
        self.session_open_price = 0.0
        self.last_trade_bar = -1
        self.cooldown_bars = 5 # Cooldown after a trade

        self.swing_threshold_pct = 0.8 # % of ATR to consider a "stretched" move
        self.atr_period = 20
        self.ema_period = 20 # For trend filtering

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_price = ctx.bar.close

        # Reset session data at the start of a new session day (UTC 00:00)
        if current_hour == 0 and self.session_bar_count != 0:
            self.session_bar_count = 0
            self.session_high = -math.inf
            self.session_low = math.inf
            self.session_open_price = 0.0
            # Reset last_trade_bar to allow new trades each day
            self.last_trade_bar = -1 

        # Update session stats during the active session hours
        if current_hour >= self.entry_hour_start and current_hour <= self.exit_hour:
            if self.session_bar_count == 0:
                self.session_open_price = ctx.bar.open
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)
            self.session_bar_count += 1
        
        # --- Exit Logic ---
        if ctx.has_position():
            # Hard exit at end of session
            if current_hour >= self.exit_hour:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "session_time_exit",
                    "exit_hour": current_hour,
                    "entry_price": ctx.position["entry_price"]
                })
            
            # Simple stop-loss re-evaluation (optional, platform handles SL)
            # You could add custom trailing stops here if desired.
            
            return None # Hold position

        # --- Entry Logic ---
        # Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Only consider entries during specified window
        if current_hour < self.entry_hour_start or current_hour > self.entry_hour_end:
            return None
        
        # Only one entry per day (session)
        if self.last_trade_bar != -1 and ctx.bar.timestamp.day == ctx.bars[self.last_trade_bar].timestamp.day:
            return None

        # Filters
        fear_greed = ctx.features.get("fear_greed_index", 50)
        eth_return = ctx.features.get("eth_return_pct", 0.0)
        
        # Avoid trading in crisis regimes or strong trends
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        if abs(eth_return) > 3.0: # Avoid very strong 24h moves
            return None

        # Calculate ATR for dynamic swing threshold
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None:
            return None
        
        # Calculate EMA for trend filtering
        ema_20 = self._ema(closes, self.ema_period)
        if ema_20 is None:
            return None

        session_range = self.session_high - self.session_low
        session_midpoint = (self.session_high + self.session_low) / 2.0
        
        # Condition for a "stretched" move
        # We need at least a few bars to establish a session range
        if self.session_bar_count < 3:
            return None

        swing_magnitude = self.swing_threshold_pct * atr

        # Long entry: price is significantly below session open and EMA, but bouncing towards midpoint
        # Stretched down AND price has recently moved up from session low
        if current_price < self.session_open_price - swing_magnitude and \
           current_price < ema_20 and \
           (ctx.bar.close - ctx.bar.open) > 0 and \
           (current_price - self.session_low) < (session_range * 0.3): # Close to session low, but trying to recover
            
            # Additional check: current price is below the session midpoint
            if current_price < session_midpoint:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("long", confidence=0.7, metadata={
                    "reason": "euro_session_fade_long",
                    "current_hour": current_hour,
                    "session_open": self.session_open_price,
                    "session_low": self.session_low,
                    "session_mid": session_midpoint,
                    "current_price": current_price,
                    "atr": atr,
                    "swing_magnitude": swing_magnitude,
                    "ema20": ema_20,
                    "fear_greed": fear_greed
                })

        # Short entry: price is significantly above session open and EMA, but stalling/reversing towards midpoint
        # Stretched up AND price has recently moved down from session high
        if current_price > self.session_open_price + swing_magnitude and \
           current_price > ema_20 and \
           (ctx.bar.close - ctx.bar.open) < 0 and \
           (self.session_high - current_price) < (session_range * 0.3): # Close to session high, but trying to revert
            
            # Additional check: current price is above the session midpoint
            if current_price > session_midpoint:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("short", confidence=0.7, metadata={
                    "reason": "euro_session_fade_short",
                    "current_hour": current_hour,
                    "session_open": self.session_open_price,
                    "session_high": self.session_high,
                    "session_mid": session_midpoint,
                    "current_price": current_price,
                    "atr": atr,
                    "swing_magnitude": swing_magnitude,
                    "ema20": ema_20,
                    "fear_greed": fear_greed
                })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log or adapt based on position outcome
        pass