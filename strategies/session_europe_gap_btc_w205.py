from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class GapReversionBTC(Strategy):
    METADATA = {
        "name": "BTC Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for up to 4 hours hold
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar_index = -100 # Cooldown period
        self.prior_session_close = None
        self.session_open_price = None
        self.last_session_day = -1
        self.gap_threshold_bps = 20 # Minimum gap size in bps
        self.cooldown_bars = 4 # Cooldown after a trade

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_day = ctx.bar.timestamp.day

        # Session logic: European morning (UTC 7-12)
        # Session close at UTC 7 (i.e., bar for 6:00-6:59 UTC)
        # Session open at UTC 7 (i.e., bar for 7:00-7:59 UTC)

        # Detect new session open (UTC 7:00)
        if current_hour == 7 and current_day != self.last_session_day:
            # New session. The prior_session_close is from the bar that just closed (6:00-6:59 UTC)
            # which is ctx.closes(2)[0] if we're looking at the 7:00 bar.
            # However, for simplicity and to ensure data availability, we'll use the close from the
            # previous bar (the 6:00 UTC bar) as the "prior session close".
            # The current bar's open (7:00 UTC) is the "session open".
            if len(ctx.closes(2)) >= 2:
                self.prior_session_close = ctx.closes(2)[-2] # Close of the 6:00 UTC bar
            else:
                self.prior_session_close = None # Not enough data

            self.session_open_price = ctx.bar.open
            self.last_session_day = current_day
            # Reset position tracking for the new session
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "session_reset", "current_hour": current_hour})

        # Exit logic for any open position before session end (UTC 12:00)
        if ctx.has_position() and current_hour >= 12:
            return ctx.signal("flat", confidence=0.7, metadata={"reason": "time_based_exit_eod", "current_hour": current_hour})

        # Entry logic: Only during European morning (UTC 8-11)
        if ctx.has_position() or not (8 <= current_hour <= 11):
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Main strategy logic: Gap Reversion
        if self.prior_session_close and self.session_open_price:
            gap_bps = ((self.session_open_price - self.prior_session_close) / self.prior_session_close) * 10000

            # Check for a significant gap
            if abs(gap_bps) > self.gap_threshold_bps:
                # Check for lack of continuation (price moving back towards prior close)
                # We use a short-term SMA to check for reversal
                sma_period = 5
                sma_closes = ctx.closes(sma_period)
                if len(sma_closes) < sma_period:
                    return None
                
                current_sma = self._sma(closes, sma_period)
                if current_sma is None:
                    return None

                # Additional filter: funding rate to gauge sentiment/positioning
                funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
                
                # Long signal: Gap down, price reversing up towards prior close
                if gap_bps < -self.gap_threshold_bps: # Gap down
                    if current_sma > closes[-2] and ctx.bar.close > current_sma: # Price reversing up
                        # Also check if funding is not excessively negative (suggests too much shorting already)
                        if funding_rate > -0.00005: # Not too negative funding
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "long",
                                confidence=0.65,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                metadata={
                                    "reason": "gap_down_reversion_long",
                                    "gap_bps": round(gap_bps, 2),
                                    "prior_close": round(self.prior_session_close, 2),
                                    "session_open": round(self.session_open_price, 2),
                                    "current_close": round(ctx.bar.close, 2),
                                    "sma_5": round(current_sma, 2),
                                    "funding_rate": round(funding_rate, 5),
                                }
                            )

                # Short signal: Gap up, price reversing down towards prior close
                elif gap_bps > self.gap_threshold_bps: # Gap up
                    if current_sma < closes[-2] and ctx.bar.close < current_sma: # Price reversing down
                        # Also check if funding is not excessively positive (suggests too much longing already)
                        if funding_rate < 0.00005: # Not too positive funding
                            self.last_trade_bar_index = ctx.bar_index
                            return ctx.signal(
                                "short",
                                confidence=0.65,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                metadata={
                                    "reason": "gap_up_reversion_short",
                                    "gap_bps": round(gap_bps, 2),
                                    "prior_close": round(self.prior_session_close, 2),
                                    "session_open": round(self.session_open_price, 2),
                                    "current_close": round(ctx.bar.close, 2),
                                    "sma_5": round(current_sma, 2),
                                    "funding_rate": round(funding_rate, 5),
                                }
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or reset state if needed
        pass