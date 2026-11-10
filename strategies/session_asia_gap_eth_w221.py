from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaSessionGapReversion(Strategy):
    METADATA = {
        "name": "Asia Session Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Aim for 1-hour bar, so 1-8 hours hold
        "warmup_bars": 24, # Need a full day of bars for session logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_asia_close = None
        self.last_trade_bar_index = -1
        self.cooldown_period = 6 # Cooldown for 6 bars (6 hours) after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Update last_asia_close at the end of the Asia session (UTC 8)
        if current_hour == 8 and ctx.bar.timestamp.minute == 0:
            # Ensure we only capture the close once per day
            # This relies on the 1-hour bar having a consistent timestamp at the start of the hour
            self.last_asia_close = ctx.bar.close
            return None # No trading at the end of the session, just update reference

        # Trading logic during Asia session (UTC 0-8)
        if 0 <= current_hour < 8:
            if self.last_asia_close is None:
                return None

            # Cooldown logic
            if ctx.bar_index - self.last_trade_bar_index < self.cooldown_period:
                return None

            # If we have an open position, manage it
            if ctx.has_position():
                # Exit before the end of the Asia session (UTC 8) if still open
                # This ensures we don't hold positions across session boundaries unnecessarily
                if current_hour == 7 and ctx.bar.timestamp.minute == 0:
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour})
                return ctx.signal("hold") # Hold if within session and no specific exit trigger

            # Entry logic: Check for gap reversion
            # The "open" of the current bar (which is the first bar of a new session if current_hour == 0)
            # or the current price if the gap is forming within the session
            current_price = ctx.bar.open if current_hour == 0 else ctx.bar.close

            # Calculate gap from last Asia session close to current price
            gap_pct = ((current_price - self.last_asia_close) / self.last_asia_close) * 100

            # Define thresholds for a significant gap (e.g., 0.5% - 1%)
            # These are heuristic and need backtest tuning
            long_gap_threshold = -0.75 # Price significantly below last Asia close
            short_gap_threshold = 0.75  # Price significantly above last Asia close

            # Additional filters for confirmation/avoiding whipsaws
            # Use a short-period EMA to check for immediate direction
            # Using EMA5 as a short-term trend filter
            ema_period = 5
            ema_values = [self._ema(closes[:i+1], ema_period) for i in range(len(closes)) if self._ema(closes[:i+1], ema_period) is not None]
            if len(ema_values) < 2:
                return None
            current_ema = ema_values[-1]
            prev_ema = ema_values[-2]

            # Long signal: Significant negative gap and price showing signs of reversal (e.g., current close above short-term EMA)
            if gap_pct < long_gap_threshold:
                if current_price > current_ema and current_ema > prev_ema: # Price above EMA, EMA rising
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.8,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_session_gap_reversion_long",
                            "gap_pct": round(gap_pct, 2),
                            "last_asia_close": round(self.last_asia_close, 2),
                            "current_price": round(current_price, 2),
                            "current_ema5": round(current_ema, 2),
                            "regime": ctx.regime,
                        }
                    )

            # Short signal: Significant positive gap and price showing signs of reversal (e.g., current close below short-term EMA)
            elif gap_pct > short_gap_threshold:
                if current_price < current_ema and current_ema < prev_ema: # Price below EMA, EMA falling
                    self.last_trade_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.8,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_session_gap_reversion_short",
                            "gap_pct": round(gap_pct, 2),
                            "last_asia_close": round(self.last_asia_close, 2),
                            "current_price": round(current_price, 2),
                            "current_ema5": round(current_ema, 2),
                            "regime": ctx.regime,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Adaptive logic based on position close
        pass

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        # Calculate initial SMA for the first 'period' values
        ema = sum(values[:period]) / period
        # Apply EMA formula for subsequent values
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema