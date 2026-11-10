from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import collections

class EuropeOpenFade(Strategy):
    METADATA = {
        "name": "Europe Open Fade",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Max hold 3 hours, aim to exit by 12 UTC
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.long_ema_period = 20
        self.short_ema_period = 5
        self.rsi_period = 14
        self.session_open_hour = 7 # UTC 7 AM
        self.session_close_hour = 12 # UTC 12 PM
        self.last_trade_bar = -1
        self.cooldown_bars = 5 # Wait 5 bars after a trade before new entry
        self.daily_session_high = -1.0
        self.daily_session_low = float('inf')
        self.session_open_price = 0.0
        self.session_bar_index = -1
        self.has_traded_session = False

    def _ema(self, values: list[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period # Simple average for initial EMA
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list[float], period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
    
    def _stddev(self, values: list[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.long_ema_period + self.rsi_period + 1)
        highs = ctx.highs(self.long_ema_period + self.rsi_period + 1)
        lows = ctx.lows(self.long_ema_period + self.rsi_period + 1)

        if len(closes) < self.long_ema_period + self.rsi_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day # Simple way to detect new day

        # Reset session data at the start of a new day
        if current_hour == self.session_open_hour and ctx.bar_index != self.session_bar_index:
            self.daily_session_high = ctx.bar.high
            self.daily_session_low = ctx.bar.low
            self.session_open_price = ctx.bar.open
            self.session_bar_index = ctx.bar_index
            self.has_traded_session = False
        elif ctx.bar_index == self.session_bar_index: # Update session high/low for the current session
            self.daily_session_high = max(self.daily_session_high, ctx.bar.high)
            self.daily_session_low = min(self.daily_session_low, ctx.bar.low)

        # Exit logic for existing positions
        if ctx.has_position():
            # Exit if session closes
            if current_hour >= self.session_close_hour and ctx.position_direction() is not None:
                return ctx.signal("flat", confidence=0.9, metadata={"reason": "session_close_time", "current_hour": current_hour})
            
            # If in a position, just hold unless session closes
            return ctx.signal("hold")

        # Cooldown period
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None
        
        # Only trade during European morning session
        if not (self.session_open_hour <= current_hour < self.session_close_hour):
            return None
        
        # Don't trade more than once per session
        if self.has_traded_session:
            return None

        # Calculate indicators
        long_ema = self._ema(closes, self.long_ema_period)
        short_ema = self._ema(closes, self.short_ema_period)
        rsi = self._rsi(closes, self.rsi_period)
        
        if long_ema is None or short_ema is None or rsi is None:
            return None

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        fear_greed_index = ctx.features.get("fear_greed_index", 50)

        # Avoid trading in crisis regimes or strong trends
        if regime in ["CRISIS", "MELTDOWN"] or abs(btc_return_pct) > 1.5: # If BTC 24h return > 1.5%, avoid fading
            return None
        
        # Avoid fading extremely fearful/greedy markets
        if fear_greed_index < 20 or fear_greed_index > 80:
            return None

        # Wildcard: Fade stretched session moves
        # A stretched move is when the price moves significantly from the open in one direction,
        # but RSI indicates it's overbought/oversold, suggesting a potential reversion to the mean.
        
        # Calculate session move
        session_move_up = ctx.bar.close - self.session_open_price
        session_move_down = self.session_open_price - ctx.bar.close

        # Use 20-bar std dev as a proxy for recent volatility to define "stretched"
        volatility_std = self._stddev(closes, 20)
        if volatility_std is None:
            return None
        
        # Define "stretched" as a move exceeding 1.5x the recent standard deviation
        stretched_threshold = 1.5 * volatility_std

        signal_dir = None
        confidence = 0.0
        reason = "no_signal"

        # Long entry: Fade a stretched move down
        if session_move_down > stretched_threshold and rsi < 35 and short_ema < long_ema:
            # Check if price is near session low, indicating a potential bounce
            if (ctx.bar.close - self.daily_session_low) / self.daily_session_low < 0.001: # Within 0.1% of session low
                signal_dir = "long"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1) # Add a touch of randomness
                reason = "fade_stretched_session_down_rsi_oversold"
                self.has_traded_session = True

        # Short entry: Fade a stretched move up
        elif session_move_up > stretched_threshold and rsi > 65 and short_ema > long_ema:
            # Check if price is near session high, indicating a potential pullback
            if (self.daily_session_high - ctx.bar.close) / self.daily_session_high < 0.001: # Within 0.1% of session high
                signal_dir = "short"
                confidence = 0.7 + ctx.rng.uniform(-0.1, 0.1)
                reason = "fade_stretched_session_up_rsi_overbought"
                self.has_traded_session = True

        if signal_dir:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_dir,
                confidence=min(1.0, max(0.0, confidence)),
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_open_price": self.session_open_price,
                    "session_move": session_move_up if signal_dir == "short" else session_move_down,
                    "stretched_threshold": stretched_threshold,
                    "rsi": rsi,
                    "short_ema": short_ema,
                    "long_ema": long_ema,
                    "regime": regime,
                    "btc_return_pct": btc_return_pct,
                    "fear_greed_index": fear_greed_index,
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Not used for adaptive logic in this strategy, but could be used to adjust cooldown, etc.
        pass