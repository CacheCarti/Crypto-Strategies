from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthUsSessionGapReversion(Strategy):
    METADATA = {
        "name": "ETH US Session Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Max hold 2 hours within session
        "warmup_bars": 20, # Need prior closes and opens for gap detection
        "required_features": [] # No external features needed for this logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_us_close = None
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] - 1 # Allow trading immediately after warmup
        self.cooldown_bars = 4 # Cooldown period after a trade

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"] or len(opens) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Update last_us_close at the end of the US session (UTC 23:00 bar closes at 24:00)
        # The bar at 23:00 UTC (hour 23) is the last bar of the trading day for US session logic
        if current_hour == 23:
            self.last_us_close = ctx.bar.close

        # Trading window: Late US session (UTC 18:00 to 23:00)
        # Entry logic fires on the bar starting at 18:00 UTC up to 22:00 UTC
        # We need the previous day's US close (from hour 23) and the current bar's open.
        # This implies we can only trade from the bar starting at 00:00 UTC, if self.last_us_close is set.
        # Let's refine: The "gap lacks continuation" implies we need to see the open of the new session.
        # The US session is generally considered 13:00 UTC to 22:00 UTC.
        # Let's define the "late US session" for trading as 18:00 UTC to 23:00 UTC.
        # The gap is between the previous day's 23:00 UTC close and the current bar's 18:00 UTC open.

        if ctx.has_position():
            # If we are in the last hour of our trading window (23:00 UTC bar), try to flatten
            if current_hour == 23:
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "session_end_flatten", "current_hour": current_hour})
            return None # Hold position within the session

        # Cooldown period check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Determine the previous US session close
        # This assumes we want to compare with the previous *trading day's* US close.
        # The self.last_us_close is set at 23:00 UTC.
        # We need to ensure it's from a *different* day than the current trading opportunity.
        # For a new trading opportunity, we'd check from 18:00 UTC onwards.
        # So if current_hour is 18, 19, 20, 21, 22, we need the last_us_close from the *previous* calendar day.
        # This logic ensures `self.last_us_close` is the close of the 23:00 UTC bar from the previous day.

        # Trading window: 18:00 UTC to 22:00 UTC (inclusive of 22:00 bar, which closes at 23:00)
        if 18 <= current_hour <= 22 and self.last_us_close is not None:
            current_open = ctx.bar.open
            current_close = ctx.bar.close
            gap_threshold_bps = 5.0 # Minimum gap size in bps

            gap_pct = ((current_open - self.last_us_close) / self.last_us_close) * 100
            
            # Long entry condition: significant negative gap and current bar shows reversal (closes higher than open)
            if gap_pct < -gap_threshold_bps / 100.0: # Gap down
                # Check for continuation failure: current bar should close higher than its open
                # or close back towards the prior_us_close
                if current_close > current_open:
                    # Further check: has the price bounced significantly from the low?
                    # Or is it currently closing back above the open?
                    # And is the current close still below the previous US close?
                    if current_close > current_open and current_close < self.last_us_close:
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.65,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "us_session_gap_down_reversion",
                                "gap_pct": round(gap_pct * 100, 2),
                                "last_us_close": self.last_us_close,
                                "current_open": current_open,
                                "current_close": current_close,
                                "entry_hour": current_hour
                            }
                        )

            # Short entry condition: significant positive gap and current bar shows reversal (closes lower than open)
            elif gap_pct > gap_threshold_bps / 100.0: # Gap up
                # Check for continuation failure: current bar should close lower than its open
                # or close back towards the prior_us_close
                if current_close < current_open:
                    # Further check: has the price dropped significantly from the high?
                    # Or is it currently closing back below the open?
                    # And is the current close still above the previous US close?
                    if current_close < current_open and current_close > self.last_us_close:
                        self.last_trade_bar_index = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.65,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "us_session_gap_up_reversion",
                                "gap_pct": round(gap_pct * 100, 2),
                                "last_us_close": self.last_us_close,
                                "current_open": current_open,
                                "current_close": current_close,
                                "entry_hour": current_hour
                            }
                        )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We log the close reason and reset any daily state if needed.
        # For this strategy, the cooldown is handled in on_bar.
        # No specific state needs to be reset here.
        pass