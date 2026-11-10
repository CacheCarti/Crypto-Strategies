from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeanSessionFade(Strategy):
    METADATA = {
        "name": "EuropeanSessionFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max 2-hour hold, exit by 12 UTC
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_start_hour = 7  # UTC 7:00
        self.session_end_hour = 12   # UTC 12:00
        self.entry_cooldown_bars = 5 # Cooldown after an exit before new entry
        self.last_trade_bar_index = -self.entry_cooldown_bars - 100 # Initialize far in the past

        self.ema_period_fast = 10
        self.ema_period_slow = 30
        self.rsi_period = 14
        self.atr_period = 20

        # State for entry logic
        self.last_bar_session_active = False
        self.session_open_price = None
        self.session_high = None
        self.session_low = None
        self.session_start_bar_index = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Calculate initial average gain/loss
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Avoid division by zero
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        highs = ctx.highs(self.warmup_bars + 1)
        lows = ctx.lows(self.warmup_bars + 1)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        is_session_active = self.session_start_hour <= current_hour < self.session_end_hour
        
        # Reset session state at the start of a new session
        if is_session_active and not self.last_bar_session_active:
            self.session_open_price = ctx.bar.open
            self.session_high = ctx.bar.high
            self.session_low = ctx.bar.low
            self.session_start_bar_index = ctx.bar_index
            # ctx.logger.info(f"Session started at {ctx.bar.timestamp}. Open: {self.session_open_price}")
        elif not is_session_active and self.last_bar_session_active:
            # Session just ended, reset all session-related state
            self.session_open_price = None
            self.session_high = None
            self.session_low = None
            self.session_start_bar_index = -1
            # ctx.logger.info(f"Session ended at {ctx.bar.timestamp}")

        self.last_bar_session_active = is_session_active

        # Update session high/low if active
        if is_session_active and self.session_open_price is not None:
            self.session_high = max(self.session_high, ctx.bar.high)
            self.session_low = min(self.session_low, ctx.bar.low)

        # Exit logic: Flat position if session ends or if a position has been held too long
        if ctx.has_position():
            if not is_session_active and ctx.position_direction() is not None:
                # ctx.logger.info(f"Exiting position due to session end at {ctx.bar.timestamp}")
                return ctx.signal("flat", confidence=1.0, metadata={"reason": "session_end_exit", "current_hour": current_hour})
            
            # Additional safety for long holds within the session if needed, though declared_hold_seconds should handle this
            # if ctx.bar_index - ctx.position["entry_bar_index"] > self.METADATA["declared_hold_seconds"] / (60 * 60): # Example for hourly bars
            #    return ctx.signal("flat", confidence=0.8, metadata={"reason": "max_session_hold_time_exit"})

        # Entry logic
        if ctx.has_position():
            return None # Already in a position

        if ctx.bar_index - self.last_trade_bar_index < self.entry_cooldown_bars:
            return None # Cooldown active

        if not is_session_active or self.session_open_price is None or self.session_start_bar_index == ctx.bar_index:
            return None # Only trade within session, after first bar of session

        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Avoid trading in crisis regimes or during strong trends
        if regime in ["CRISIS", "MELTDOWN"] or abs(eth_return_pct) > 3.0: # Avoid strong moves
            return None
        
        # Avoid trading if fear is extreme, indicating potential for continued downside
        if fear_greed_index < 20: # Extreme Fear
            return None

        # Calculate indicators
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if any(x is None for x in [ema_fast, ema_slow, rsi, atr]):
            return None

        # Session structure analysis
        current_price = ctx.bar.close
        session_range = self.session_high - self.session_low
        
        # Check for unusually stretched moves
        # If price is significantly far from session open relative to ATR
        stretch_threshold_atr = 1.5 # e.g., 1.5 * ATR
        stretch_from_open_long = (current_price - self.session_open_price) / atr if atr > 0 else 0
        stretch_from_open_short = (self.session_open_price - current_price) / atr if atr > 0 else 0

        # Fading logic
        signal_direction: Optional[str] = None
        confidence = 0.0
        reason = ""
        
        # FADE LONG: Price stretched high, but momentum indicators show weakness or overbought
        if stretch_from_open_long > stretch_threshold_atr and current_price > self.session_open_price:
            # Price is stretched up. Look for signs to short (fade long).
            if rsi > 70 and ema_fast < ema_slow: # RSI overbought, but slower EMA is below faster, suggesting potential stall
                signal_direction = "short"
                confidence = min(0.7, (rsi - 70) / 10 * 0.7 + (current_price - self.session_open_price) / self.session_open_price * 100 * 0.01)
                reason = "session_stretched_up_rsi_overbought_ema_cross_bearish"
        
        # FADE SHORT: Price stretched low, but momentum indicators show strength or oversold
        elif stretch_from_open_short > stretch_threshold_atr and current_price < self.session_open_price:
            # Price is stretched down. Look for signs to long (fade short).
            if rsi < 30 and ema_fast > ema_slow: # RSI oversold, but slower EMA above faster, suggesting potential stall
                signal_direction = "long"
                confidence = min(0.7, (30 - rsi) / 10 * 0.7 + (self.session_open_price - current_price) / self.session_open_price * 100 * 0.01)
                reason = "session_stretched_down_rsi_oversold_ema_cross_bullish"

        # Only enter if confidence is above a threshold and not trading against strong trend
        if signal_direction and confidence > 0.5:
            # Additional check: do not fade if current price is breaking out of recent range
            # Use a simple measure of recent volatility
            recent_high = max(highs[-5:])
            recent_low = min(lows[-5:])
            
            if signal_direction == "short" and current_price == recent_high: # Likely a breakout, not a stall
                return None
            if signal_direction == "long" and current_price == recent_low: # Likely a breakout, not a stall
                return None

            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "current_hour": current_hour,
                    "session_open_price": self.session_open_price,
                    "current_price": current_price,
                    "stretch_from_open_atr": stretch_from_open_long if signal_direction == "short" else stretch_from_open_short,
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "atr": atr,
                    "regime": regime,
                    "fear_greed_index": fear_greed_index
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Update last trade bar index to enforce cooldown
        self.last_trade_bar_index = ctx.bar_index
        # ctx.logger.info(f"Position closed. Return: {position['return_bps']:.2f} bps. Bars held: {position['bars_held']}")