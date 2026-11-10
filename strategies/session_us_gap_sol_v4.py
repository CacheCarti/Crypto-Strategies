from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionGapReversion(Strategy):
    METADATA = {
        "name": "US Session Gap Reversion (SOL)",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600, # Intended hold for 1-hour bars, or until session end
        "warmup_bars": 30,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"] # Use these for regime gating
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_us_session_close = None
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 5 # Cooldown after a trade
        self.entry_threshold_bps = 50 # Minimum gap size in bps to consider a trade
        self.max_gap_bps = 250 # Maximum gap size to consider, avoid extreme gaps
        self.us_session_start_hour = 13 # UTC 13:00 (9 AM ET)
        self.us_session_end_hour = 21 # UTC 21:00 (5 PM ET)
        self.min_confidence = 0.6
        self.max_confidence = 0.9

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"] + 1) # Need at least 2 bars for gap calculation
        if len(closes) <= self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close

        # Daily Cooldown: Only one entry per day (defined by US session boundaries)
        if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
            return None

        # --- Regime Filters ---
        # Avoid trading in extreme fear or greed
        fear_greed_index = ctx.features.get("fear_greed_index", 50)
        if fear_greed_index < 20 or fear_greed_index > 80:
            return None # Stand down in extreme sentiment

        # Avoid trading against strong trend or in high volatility
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")
        if trend_regime != "neutral" and trend_regime != ctx.position_direction():
             # If we are in a strong trend, only trade with it, or if neutral
            return None
        if regime in ["CRISIS", "HIGH_VOL"]:
            return None # Stand down in crisis or very high volatility

        # --- Session Logic ---
        # 1. Store last US session close price
        if current_hour == self.us_session_end_hour - 1: # One hour before session closes
            self.last_us_session_close = current_close

        # 2. Check for gap at US session open (13:00 UTC)
        if current_hour == self.us_session_start_hour and self.last_us_session_close is not None:
            # Only consider opening a new position if no position is open
            if ctx.has_position():
                return None

            gap_to_open = (current_close - self.last_us_session_close) / self.last_us_session_close * 10000 # in bps

            # LONG signal: Price opened significantly below last session close
            if gap_to_open < -self.entry_threshold_bps and gap_to_open > -self.max_gap_bps:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=self.min_confidence,
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_session_gap_reversion_long",
                        "gap_bps": gap_to_open,
                        "last_session_close": self.last_us_session_close,
                        "current_open": ctx.bar.open,
                        "current_close": current_close,
                        "fear_greed_index": fear_greed_index,
                        "trend_regime": trend_regime,
                    }
                )
            # SHORT signal: Price opened significantly above last session close
            elif gap_to_open > self.entry_threshold_bps and gap_to_open < self.max_gap_bps:
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=self.min_confidence,
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "us_session_gap_reversion_short",
                        "gap_bps": gap_to_open,
                        "last_session_close": self.last_us_session_close,
                        "current_open": ctx.bar.open,
                        "current_close": current_close,
                        "fear_greed_index": fear_greed_index,
                        "trend_regime": trend_regime,
                    }
                )

        # 3. Exit existing position before US session closes (if not already exited by SL/TP)
        if ctx.has_position() and current_hour == self.us_session_end_hour:
            self.last_trade_bar_index = ctx.bar_index # Reset cooldown
            return ctx.signal(
                "flat",
                confidence=self.max_confidence, # High confidence to exit
                metadata={
                    "reason": "session_end_exit",
                    "entry_price": ctx.position["entry_price"],
                    "current_price": current_close,
                    "direction": ctx.position["direction"],
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset cooldown on any position close
        self.last_trade_bar_index = ctx.bar_index
        # Log for analysis
        ctx.logger.info(f"Position closed: {position['direction']} {position['return_bps']:.2f} bps, held {position['bars_held']} bars. Reason: {position['exit_reason']}")