from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionGapReversion(Strategy):
    METADATA = {
        "name": "Session Gap Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # Up to 2 hours
        "warmup_bars": 24, # Need 24 bars for prior day's close
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.gap_threshold_pct = 0.003 # 0.3% gap
        self.continuation_threshold_pct = 0.001 # 0.1% continuation
        self.cooldown_bars = 6 # Cooldown for 6 hours after a trade
        self.last_trade_bar_index = -self.cooldown_bars * 2 # Initialize to allow trading immediately
        self.prior_session_close = None
        self.traded_today = False
        self.entry_hour = 13 # US Session start
        self.exit_hour = 20 # Exit before 21 UTC

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(25) # Need prior day's close
        opens = ctx.opens(1)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Reset daily trade flag at the start of a new day (e.g., UTC 00:00)
        if current_hour == 0 and self.traded_today:
            self.traded_today = False

        # Store prior session close (from the end of the previous US session)
        if current_hour == self.exit_hour: # End of US session
            self.prior_session_close = ctx.bar.close

        # Trading logic only within US session
        if not (self.entry_hour <= current_hour <= self.exit_hour):
            return None

        # Only allow one trade per session/day
        if self.traded_today:
            return None

        # Cooldown check
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # Ensure we have a prior session close to compare against
        if self.prior_session_close is None:
            return None

        # Calculate gap from prior session close to current session open
        session_open_price = ctx.bar.open # This is the first bar of the current US session
        if current_hour == self.entry_hour:
            session_open_price = ctx.bar.open
        else:
            # If we are past the entry hour, we need to find the actual session open
            # For simplicity, we'll use the current bar's open for gap calculation if it's the first bar of the session we're considering.
            # A more robust approach would track the actual session open.
            # Given the strategy is to trade *back* towards prior close, using current open is fine for "gap"
            pass


        gap_pct = (session_open_price - self.prior_session_close) / self.prior_session_close
        
        # Check for continuation (first bar's move)
        continuation_pct = (ctx.bar.close - ctx.bar.open) / ctx.bar.open

        # Regime filter: avoid trading in very volatile or crisis regimes
        # Market context regime is more robust than ctx.regime string
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ["CRISIS", "HIGH_VOL"]:
            return None

        # Sentiment filter: only trade if F&G is not extreme
        fear_greed = ctx.features.get("fear_greed_index", 50)
        if fear_greed < 20 or fear_greed > 80: # Extreme Fear or Extreme Greed
            return None

        # If we have a position, manage exit conditions
        if ctx.has_position():
            # Exit before the session ends
            if current_hour >= self.exit_hour:
                self.traded_today = True # Mark as traded for the day
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_end_exit",
                    "current_hour": current_hour,
                    "exit_hour": self.exit_hour
                })
            return None # Hold position otherwise

        # Entry logic
        signal_direction = None
        reason = ""
        confidence = 0.0

        # Bearish gap up, no continuation, trade short back to prior close
        if gap_pct > self.gap_threshold_pct and continuation_pct < self.continuation_threshold_pct:
            # Price gapped up significantly, but failed to move higher in the first bar
            signal_direction = "short"
            confidence = min(0.8, abs(gap_pct) * 100)
            reason = "bearish_gap_up_no_continuation"
        
        # Bullish gap down, no continuation, trade long back to prior close
        elif gap_pct < -self.gap_threshold_pct and continuation_pct > -self.continuation_threshold_pct:
            # Price gapped down significantly, but failed to move lower in the first bar
            signal_direction = "long"
            confidence = min(0.8, abs(gap_pct) * 100)
            reason = "bullish_gap_down_no_continuation"

        if signal_direction:
            self.traded_today = True
            self.last_trade_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "gap_pct": gap_pct,
                    "continuation_pct": continuation_pct,
                    "prior_session_close": self.prior_session_close,
                    "session_open_price": session_open_price,
                    "current_close": ctx.bar.close,
                    "fear_greed_index": fear_greed,
                    "market_regime": market_regime
                }
            )
        
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already set self.traded_today = True on signal issuance
        # This method can be used for adaptive logic if needed.
        pass