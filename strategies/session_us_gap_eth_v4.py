from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthUsSessionGapReversion(Strategy):
    METADATA = {
        "name": "ETH US Session Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended to hold for a few hours within the session
        "warmup_bars": 48,  # Need enough history for prior session data
        "required_features": [], # No external features used in this version
    }

    def initialize(self, ctx: BarContext) -> None:
        self.us_session_start_hour = 13  # UTC 13:00 (9 AM ET)
        self.us_session_end_hour = 21    # UTC 21:00 (5 PM ET)
        self.prior_session_close = None
        self.session_open_price = None
        self.trade_today = False
        self.cooldown_bars = 6
        self.last_trade_bar_index = -self.cooldown_bars - 1 # Allow trading on first bar

        # Gap threshold in percentage (e.g., 0.005 for 0.5%)
        self.gap_threshold_pct = 0.003
        # Continuation threshold in percentage (e.g., 0.002 for 0.2% away from gap direction)
        self.continuation_threshold_pct = 0.002
        self.min_gap_size_bps = 30 # Minimum gap to consider, in basis points

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(48)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Regime filtering
        # Only trade in normal or volatile regimes, avoid crisis
        if ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]:
            return None
        
        # Only trade if trend is not strongly against our reversion idea (i.e. not strong bull for short, not strong bear for long)
        trend_regime = ctx.market.get("trend_regime", "neutral")
        trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)
        
        # Reset at the start of a new day (after session end, before next session start)
        if current_hour == self.us_session_end_hour + 1: # e.g., 22:00 UTC, after session
            self.trade_today = False
            self.prior_session_close = ctx.bar.close # Capture prior session close for next day's gap calculation
        
        # Session start logic
        if current_hour == self.us_session_start_hour:
            if self.session_open_price is None: # First bar of the session
                self.session_open_price = ctx.bar.open
                # Capture prior session close if it wasn't captured at the very end of the last day
                if self.prior_session_close is None:
                    # This implies we started the backtest in the middle of the day or missed the exact hour
                    # For robustness, we can try to find the close from the previous day's end
                    # (This is a simplified approach, a more robust one would involve iterating back)
                    # For now, if no prior_session_close, we skip gap logic for this first session.
                    pass 
        elif current_hour != self.us_session_start_hour and self.session_open_price is not None:
            # Session is ongoing, reset session_open_price for next session if it's already past the start
            self.session_open_price = None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Exit logic before session end
        if ctx.has_position() and current_hour >= self.us_session_end_hour - 1: # Exit one hour before session end
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_exit", "current_hour": current_hour, "price": ctx.bar.close})

        # Entry logic: Only within US session, and only one trade per session
        if self.us_session_start_hour <= current_hour < self.us_session_end_hour and not self.trade_today:
            if self.prior_session_close is not None and self.session_open_price is not None:
                gap = (self.session_open_price - self.prior_session_close) / self.prior_session_close
                gap_bps = gap * 10000

                # Check for significant gap
                if abs(gap_bps) >= self.min_gap_size_bps:
                    current_price = ctx.bar.close
                    
                    # Gap Down Reversion (Go Long)
                    if gap < -self.gap_threshold_pct:
                        # Price has not continued down significantly (or has started to revert)
                        # And we are not in a strong bear trend
                        if (current_price > self.session_open_price * (1 - self.continuation_threshold_pct)) and \
                           not (trend_regime == "bear" and trend_confidence > 0.6):
                            self.trade_today = True
                            return ctx.signal(
                                "long",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "us_session_gap_down_reversion",
                                    "gap_pct": gap,
                                    "prior_close": self.prior_session_close,
                                    "session_open": self.session_open_price,
                                    "current_price": current_price,
                                    "trend_regime": trend_regime,
                                    "trend_confidence": trend_confidence,
                                }
                            )
                    # Gap Up Reversion (Go Short)
                    elif gap > self.gap_threshold_pct:
                        # Price has not continued up significantly (or has started to revert)
                        # And we are not in a strong bull trend
                        if (current_price < self.session_open_price * (1 + self.continuation_threshold_pct)) and \
                           not (trend_regime == "bull" and trend_confidence > 0.6):
                            self.trade_today = True
                            return ctx.signal(
                                "short",
                                confidence=0.7,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "us_session_gap_up_reversion",
                                    "gap_pct": gap,
                                    "prior_close": self.prior_session_close,
                                    "session_open": self.session_open_price,
                                    "current_price": current_price,
                                    "trend_regime": trend_regime,
                                    "trend_confidence": trend_confidence,
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar_index = ctx.bar_index
        # Optionally, adapt parameters based on performance
        # For example: if position["return_bps"] < 0: self.gap_threshold_pct += 0.0001