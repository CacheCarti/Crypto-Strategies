from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EuropeGapReversionSOL(Strategy):
    METADATA = {
        "name": "Europe Gap Reversion SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for multi-hour holds, up to end of session
        "warmup_bars": 50,              # Need enough bars for EMA and previous day's close
        "required_features": ["fear_greed_index"], # Use F&G as a sentiment filter
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.gap_threshold_bps = 50  # Minimum gap size in bps to consider
        self.no_continuation_bars = 3 # Bars to check for continuation
        self.cooldown_bars = 6      # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow trade immediately

        # Store previous session's close and open
        self.prior_session_close = None
        self.prior_session_open = None
        self.traded_today = False # Flag to trade only once per day

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period


    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) <= self.warmup_bars:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Reset traded_today flag at the start of a new day (UTC 00:00)
        if current_hour == 0 and not self.traded_today:
            self.traded_today = False

        # Store previous session's close and open
        # Assuming prior session ends at UTC 07:00, and current session starts at 07:00
        # If it's 07:00 UTC, the bar just closed at 07:00, so ctx.bar.close is the 07:00 close.
        # The 07:00 open is ctx.bar.open.
        if current_hour == 7: # Start of European session
            # This bar's open is the open of the 7-8 AM bar
            self.prior_session_open = ctx.bar.open
            # The close of the *previous* bar is the close of the 6-7 AM bar
            # which we consider the close of the prior session.
            if len(closes) >= 2:
                self.prior_session_close = closes[-2]
            else:
                self.prior_session_close = None
            self.traded_today = False # Reset for the new session
        
        # Exit logic for positions held until end of session (UTC 12:00)
        if ctx.has_position() and current_hour == 12:
            position_direction = ctx.position_direction()
            reason = f"session_end_exit_{position_direction}"
            signal_metadata = {
                "reason": reason,
                "current_price": ctx.bar.close,
                "entry_price": ctx.position["entry_price"]
            }
            return ctx.signal("flat", confidence=0.7, metadata=signal_metadata)

        # Entry logic only during European morning (UTC 7-12)
        if not (7 <= current_hour < 12):
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_trade_bar + self.cooldown_bars:
            return None

        # Only one trade per day/session
        if self.traded_today:
            return None

        # Ensure we have prior session data
        if self.prior_session_close is None or self.prior_session_open is None:
            return None

        # Calculate gap
        gap_bps = ((self.prior_session_open - self.prior_session_close) / self.prior_session_close) * 10000

        # Regime filters
        market_regime = ctx.market.get("regime", "NORMAL")
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        
        # Avoid trading in extreme crisis or high volatility if not specifically designed for it
        if market_regime in ["CRISIS", "MELTDOWN"]:
             return None
        
        # Use F&G as a general sentiment filter
        if fear_greed_index < 30 or fear_greed_index > 70: # Avoid extreme fear/greed
             return None

        # Check for gap and lack of continuation
        if abs(gap_bps) >= self.gap_threshold_bps:
            # Check price action for continuation in the first few bars of the session
            # We need at least self.no_continuation_bars + 1 bars from the start of the session (hour 7)
            if current_hour >= 7 + self.no_continuation_bars:
                # Check if the market is reversing the gap
                # For a gap up, we want to see price failing to hold gains
                if gap_bps > 0: # Gap Up
                    # If current price is below the session open, and potentially moving back towards prior close
                    # And if the last 'no_continuation_bars' bars show weakness
                    # Simple check: current close is below the open of the 7-8 AM bar
                    if ctx.bar.close < self.prior_session_open:
                        # Check if the price has moved significantly against the gap
                        # For a gap up, we expect price to go down
                        if len(closes) > self.no_continuation_bars:
                            # Check if the price has moved below the open of the current session
                            if ctx.bar.close < self.prior_session_open:
                                # Additional check: current price is closer to prior_session_close than prior_session_open
                                if abs(ctx.bar.close - self.prior_session_close) < abs(ctx.bar.close - self.prior_session_open):
                                    # Confidence based on gap size and how much it's reversed
                                    reversal_magnitude = (self.prior_session_open - ctx.bar.close) / self.prior_session_open
                                    confidence = min(0.9, max(0.5, reversal_magnitude * 5)) # Scale confidence
                                    self.last_trade_bar = ctx.bar_index
                                    self.traded_today = True
                                    return ctx.signal("short", confidence=confidence, metadata={
                                        "reason": "europe_gap_up_reversion",
                                        "gap_bps": gap_bps,
                                        "prior_close": self.prior_session_close,
                                        "session_open": self.prior_session_open,
                                        "current_close": ctx.bar.close,
                                        "market_regime": market_regime,
                                        "fear_greed_index": fear_greed_index
                                    })

                elif gap_bps < 0: # Gap Down
                    # If current price is above the session open, and potentially moving back towards prior close
                    if ctx.bar.close > self.prior_session_open:
                         if len(closes) > self.no_continuation_bars:
                            # Check if the price has moved above the open of the current session
                            if ctx.bar.close > self.prior_session_open:
                                # Additional check: current price is closer to prior_session_close than prior_session_open
                                if abs(ctx.bar.close - self.prior_session_close) < abs(ctx.bar.close - self.prior_session_open):
                                    # Confidence based on gap size and how much it's reversed
                                    reversal_magnitude = (ctx.bar.close - self.prior_session_open) / self.prior_session_open
                                    confidence = min(0.9, max(0.5, reversal_magnitude * 5)) # Scale confidence
                                    self.last_trade_bar = ctx.bar_index
                                    self.traded_today = True
                                    return ctx.signal("long", confidence=confidence, metadata={
                                        "reason": "europe_gap_down_reversion",
                                        "gap_bps": gap_bps,
                                        "prior_close": self.prior_session_close,
                                        "session_open": self.prior_session_open,
                                        "current_close": ctx.bar.close,
                                        "market_regime": market_regime,
                                        "fear_greed_index": fear_greed_index
                                    })
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing for analysis or reset any state if needed
        # For this strategy, no specific state needs resetting on close,
        # as traded_today is reset at 00:00 UTC and last_trade_bar is updated on entry.
        pass