from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionImpulseCarry(Strategy):
    METADATA = {
        "name": "SessionImpulseCarryETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 6, # Aim for a 6-hour hold max
        "warmup_bars": 50,
        "required_features": [], # No external features used for this session-based strategy
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_lookback_period = 6 # Hours for session impulse (18-24 UTC)
        self.cooldown_bars = 12 # Cooldown for 12 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars * 2 # Initialize to allow immediate trading

        # Parameters for EMA and ATR as potential filters or for stop
        self.ema_period = 20
        self.atr_period = 14
        self.atr_multiplier = 1.5

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

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.session_lookback_period + 1)
        highs = ctx.highs(self.atr_period + 1)
        lows = ctx.lows(self.atr_period + 1)

        if len(closes) < self.session_lookback_period + 1 or len(highs) < self.atr_period + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Exit logic: Close position before UTC 0 hour
        if ctx.has_position() and current_hour == 23: # Close at 23:00 UTC to be out before 00:00
            if ctx.position_direction() == "long":
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour, "position_direction": "long"})
            elif ctx.position_direction() == "short":
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_exit", "current_hour": current_hour, "position_direction": "short"})

        # Entry logic: Late US session (18-22 UTC)
        if 18 <= current_hour <= 22 and not ctx.has_position():
            session_closes = ctx.closes(self.session_lookback_period)
            session_opens = ctx.opens(self.session_lookback_period)

            if len(session_closes) < self.session_lookback_period or len(session_opens) < self.session_lookback_period:
                return None

            # Calculate session impulse from the last N bars (e.g., 18:00 to current hour)
            # We look back to the start of the session (current_hour - session_lookback_period + 1)
            # This is a simplified approach to capture the impulse up to the current bar within the session
            # Let's consider the move from the open of 18:00 to the current bar's close.
            # For simplicity, we'll use the price change over the last `session_lookback_period` bars
            # as a proxy for the session's directional impulse.
            
            # Use the close price from `session_lookback_period` bars ago as the session's "open" for impulse calculation
            # and current close as the "current price".
            
            # Ensure we have enough data for the full lookback period
            if len(ctx.closes(self.session_lookback_period)) < self.session_lookback_period:
                return None

            impulse_start_price = ctx.closes(self.session_lookback_period)[0]
            impulse_end_price = ctx.bar.close
            
            price_change_pct = ((impulse_end_price - impulse_start_price) / impulse_start_price) * 100

            # Use ATR for volatility filter and dynamic stop-loss (though SL is fixed by contract)
            current_atr = self._atr(highs, lows, closes)
            if current_atr is None:
                return None
            
            # Entry condition: significant directional impulse
            impulse_threshold_pct = 0.5 # 0.5% move over the session lookback window
            
            # Additional filter: price relative to EMA
            current_ema = self._ema(closes, self.ema_period)
            if current_ema is None:
                return None

            signal_confidence = 0.65 # Base confidence
            
            if price_change_pct > impulse_threshold_pct and ctx.bar.close > current_ema:
                # Long signal if session impulse is strong up and price is above EMA
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "late_us_session_bull_impulse_ema_confirm",
                        "current_hour": current_hour,
                        "price_change_pct": round(price_change_pct, 2),
                        "impulse_start_price": round(impulse_start_price, 2),
                        "current_close": round(ctx.bar.close, 2),
                        "ema": round(current_ema, 2),
                        "atr": round(current_atr, 2),
                    }
                )
            elif price_change_pct < -impulse_threshold_pct and ctx.bar.close < current_ema:
                # Short signal if session impulse is strong down and price is below EMA
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=signal_confidence,
                    metadata={
                        "reason": "late_us_session_bear_impulse_ema_confirm",
                        "current_hour": current_hour,
                        "price_change_pct": round(price_change_pct, 2),
                        "impulse_start_price": round(impulse_start_price, 2),
                        "current_close": round(ctx.bar.close, 2),
                        "ema": round(current_ema, 2),
                        "atr": round(current_atr, 2),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to implement cooldown properly after a close
        # This ensures the cooldown starts from the close event, not just the signal
        # However, for this strategy, we want to prevent re-entry in the same session,
        # so the last_trade_bar_index is updated on signal. This is fine.
        pass