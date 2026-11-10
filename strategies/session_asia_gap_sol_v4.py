from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaGapReversionSOL(Strategy):
    METADATA = {
        "name": "Asia Gap Reversion SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600,  # Aim for ~1 hour, but allow up to 8 hours
        "warmup_bars": 50,              # Need enough bars for EMA and prior session data
        "required_features": [],        # No external features needed for this core logic
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period = 20
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 5 # Cooldown period after a trade
        self.prior_asia_session_close = None
        self.asia_session_open_price = None
        self.entry_bar_index = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period * 2)
        opens = ctx.opens(self.ema_period * 2)

        if len(closes) < self.METADATA["warmup_bars"] or len(opens) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        market_regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Regime filtering: Only trade in NORMAL or HIGH_VOL regimes, and not in strong trends
        if market_regime not in ["NORMAL", "HIGH_VOL"]:
            return None
        if trend_regime != "neutral": # Only trade in neutral trend regime
            return None

        # Check if we are in a position
        if ctx.has_position():
            # Exit if outside Asia session (UTC 0-8) or nearing end of session
            # Or if we've held for too long (e.g., 8 hours max)
            if current_hour >= 8 or (ctx.bar_index - self.entry_bar_index) > 8: # Exit at 8 UTC or after 8 bars
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "session_end_or_max_hold", "current_hour": current_hour})
            return None # Hold position

        # Cooldown period after a trade
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        current_ema = self._ema(closes, self.ema_period)
        if current_ema is None:
            return None

        # --- Session Logic ---
        # Asia session is UTC 0-8
        # Capture prior session close (from 23:00-23:59 UTC)
        if current_hour == 0 and self.prior_asia_session_close is None:
            # The close of the last bar of the previous day (UTC 23:00) is what we consider the prior session close
            # We need to look back to the last bar of the previous day if available
            if len(closes) >= 2 and ctx.bar.timestamp.hour == 0: # If current bar is 00:00, then closes[-2] is 23:00
                # Assuming 1-hour bars, closes[-2] is the 23:00 bar's close from previous day
                self.prior_asia_session_close = closes[-2]
                self.asia_session_open_price = ctx.bar.open # The 00:00 UTC open

        # Reset for a new day if we've passed the Asia session
        if current_hour >= 8 and self.prior_asia_session_close is not None:
            self.prior_asia_session_close = None
            self.asia_session_open_price = None

        # Entry logic: Only within Asia session (UTC 0-8) and if prior session data is available
        if 0 <= current_hour < 8 and self.prior_asia_session_close is not None and self.asia_session_open_price is not None:
            # Calculate gap percentage
            gap_bps = ((self.asia_session_open_price - self.prior_asia_session_close) / self.prior_asia_session_close) * 10000

            # Gap threshold (e.g., 50 bps)
            gap_threshold_bps = 50.0

            # Check for large gap and lack of continuation
            # Long if a significant gap down, and price shows signs of trying to revert towards prior close
            if gap_bps < -gap_threshold_bps: # Significant gap down
                # If current price is still significantly below the prior close but trying to recover
                # And is below the EMA, suggesting oversold conditions within the gap
                if ctx.bar.close < self.prior_asia_session_close * (1 - gap_threshold_bps / 10000.0) and ctx.bar.close < current_ema:
                    self.last_trade_bar_index = ctx.bar_index
                    self.entry_bar_index = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "asia_gap_down_reversion",
                            "gap_bps": gap_bps,
                            "prior_asia_close": self.prior_asia_session_close,
                            "asia_open": self.asia_session_open_price,
                            "current_close": ctx.bar.close,
                            "current_ema": current_ema,
                            "current_hour": current_hour,
                            "regime": market_regime,
                            "trend_regime": trend_regime
                        }
                    )
            # Short if a significant gap up, and price shows signs of trying to revert towards prior close
            elif gap_bps > gap_threshold_bps: # Significant gap up
                # If current price is still significantly above the prior close but trying to fall back
                # And is above the EMA, suggesting overbought conditions within the gap
                if ctx.bar.close > self.prior_asia_session_close * (1 + gap_threshold_bps / 10000.0) and ctx.bar.close > current_ema:
                    self.last_trade_bar_index = ctx.bar_index
                    self.entry_bar_index = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "asia_gap_up_reversion",
                            "gap_bps": gap_bps,
                            "prior_asia_close": self.prior_asia_session_close,
                            "asia_open": self.asia_session_open_price,
                            "current_close": ctx.bar.close,
                            "current_ema": current_ema,
                            "current_hour": current_hour,
                            "regime": market_regime,
                            "trend_regime": trend_regime
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last trade bar index to enforce cooldown after any position closes
        self.last_trade_bar_index = ctx.bar_index
        # You could add adaptive logic here, e.g., adjust confidence based on recent performance