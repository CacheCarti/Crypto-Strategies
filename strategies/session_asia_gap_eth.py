from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthAsiaGapReversion(Strategy):
    METADATA = {
        "name": "ETH Asia Gap Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # 1 hour bar, aim for 1-4 bars hold
        "warmup_bars": 20, # Need prior session close and current open
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_asia_close = None
        self.trade_cooldown_bars = 0
        self.last_trade_bar_index = -1
        self.session_traded_today = False # To ensure only one trade per session
        self.prior_close_gap = 0.0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        opens = ctx.opens(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset session_traded_today flag at the start of a new day (UTC 0)
        if current_hour == 0 and not self.session_traded_today:
            self.session_traded_today = False

        # --- Cooldown Logic ---
        if self.trade_cooldown_bars > 0:
            self.trade_cooldown_bars -= 1
            return None

        # --- Asia Session Logic (UTC 0-8) ---
        # Capture the closing price of the Asia session
        if current_hour == 8: # End of Asia session (bar for 07:00-07:59 just closed)
            self.last_asia_close = ctx.bar.close
            self.session_traded_today = False # Reset for next Asia session
            return None

        # Trading logic only within Asia session (UTC 0-7)
        if 0 <= current_hour < 8:
            if self.last_asia_close is None:
                return None # Need at least one prior Asia session close

            # Only trade if we haven't traded in this session yet
            if self.session_traded_today:
                return None

            # Get the open of the current bar (start of gap consideration)
            current_open = ctx.bar.open

            # Calculate the gap from the prior Asia session close to the current bar's open
            # This is a 'gap' in the sense of comparing the prior session's close to the current bar's open within the next session.
            # We are looking for a gap from the previous bar's close, but specifically relative to the last recorded asia session close.
            # For a 1h bar strategy, the 'gap' is less about overnight and more about previous hour vs current hour open.
            # The strategy description implies 'prior session close to next session open'.
            # Given 1h bars, we interpret "prior session close" as the `self.last_asia_close`
            # and "next session open" as the `current_open` of a bar within the Asia session.

            # We need the previous bar's close to determine if there's a *continuation*
            # of the gap or if it's reverting.
            if len(closes) < 2:
                return None
            prev_close = closes[-2]

            # Calculate the gap from the previous bar's close to the current bar's open
            # This is a standard intraday gap.
            current_gap_pct = ((current_open - prev_close) / prev_close) * 100

            # Define significant gap threshold (e.g., 0.1% to 0.5% for 1h ETH)
            # This is an exploration, so we start with a moderate threshold.
            significant_gap_threshold_pct = 0.2

            # Check for a large gap lacking continuation (reversion idea)
            # If the current bar opened significantly away from the previous close,
            # and the price is now moving back towards that previous close, or
            # the gap wasn't sustained.

            # We want to trade back towards the prior_close_gap if the current bar
            # opened significantly up or down and then failed to continue.
            # Let's consider the gap between the prior Asia session close and the current bar's open.
            gap_from_asia_close_pct = ((current_open - self.last_asia_close) / self.last_asia_close) * 100

            # Price action within the current bar relative to its open
            # If current price is below open after a big UP gap, or above open after a big DOWN gap
            if ctx.bar.high > current_open and ctx.bar.low < current_open: # Some movement
                # Long signal: Significant negative gap from prior Asia close AND current price is moving up from open (reversion)
                if gap_from_asia_close_pct < -significant_gap_threshold_pct and ctx.bar.close > current_open:
                    if not ctx.has_position():
                        self.session_traded_today = True
                        self.trade_cooldown_bars = 4 # Cooldown for 4 bars (4 hours)
                        return ctx.signal(
                            "long",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "asia_session_gap_reversion_long",
                                "gap_from_asia_close_pct": gap_from_asia_close_pct,
                                "current_open": current_open,
                                "current_close": ctx.bar.close,
                                "last_asia_close": self.last_asia_close,
                                "current_hour": current_hour,
                            }
                        )

                # Short signal: Significant positive gap from prior Asia close AND current price is moving down from open (reversion)
                elif gap_from_asia_close_pct > significant_gap_threshold_pct and ctx.bar.close < current_open:
                    if not ctx.has_position():
                        self.session_traded_today = True
                        self.trade_cooldown_bars = 4 # Cooldown for 4 bars (4 hours)
                        return ctx.signal(
                            "short",
                            confidence=0.7,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "asia_session_gap_reversion_short",
                                "gap_from_asia_close_pct": gap_from_asia_close_pct,
                                "current_open": current_open,
                                "current_close": ctx.bar.close,
                                "last_asia_close": self.last_asia_close,
                                "current_hour": current_hour,
                            }
                        )

        # --- Exit before UTC 8 or on structure stop (implied by SL/TP) ---
        # If we have a position and it's the last bar of the Asia session (UTC 7), exit
        if ctx.has_position() and current_hour == 7: # Bar for 07:00-07:59
            self.trade_cooldown_bars = 1 # Cooldown for 1 hour after forced exit
            return ctx.signal(
                "flat",
                confidence=0.8,
                metadata={
                    "reason": "exit_before_asia_session_end",
                    "current_hour": current_hour,
                    "current_close": ctx.bar.close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset session_traded_today if position closes for any reason, allowing re-entry if conditions are met
        # (though cooldown will prevent immediate re-entry)
        self.session_traded_today = False
        # A longer cooldown after a position closes, regardless of win/loss, to prevent overtrading
        self.trade_cooldown_bars = 6 # Cooldown for 6 bars (6 hours)