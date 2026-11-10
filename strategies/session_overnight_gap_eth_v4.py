from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthOvernightGap(Strategy):
    METADATA = {
        "name": "EthOvernightGap",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Roughly 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_session_start_hour_utc = 0
        self.overnight_session_end_hour_utc = 6 # overnight session ends at 06:00 UTC
        self.us_session_start_hour_utc = 13 # US session starts at 13:00 UTC (9 AM ET)

        self.gap_threshold_bps = 50  # Minimum gap in basis points to consider
        self.continuation_threshold_bps = 20 # How much price moves in direction of gap after open
        self.cooldown_bars = 6 # Wait 6 bars (6 hours) after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars

        self.ema_period = 20 # For trend filtering
        self.rsi_period = 14 # For oversold/overbought confirmation
        
        # Store last session's close to detect gaps
        self.prior_session_close: Optional[float] = None
        self.prior_session_end_bar_index: Optional[int] = None
        self.trade_attempted_today = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        # Use a longer history for initial EMA calculation for better stability
        if len(gains) < period * 2: # Need enough data for initial average
            return None

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        # Simple moving average for RSI initial calculation
        for i in range(len(gains) - period):
            avg_gain = (avg_gain * (period - 1) + gains[i + period]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i + period]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0 # Prevent division by zero
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_hour = ctx.bar.timestamp.hour
        current_close = ctx.bar.close
        current_open = ctx.bar.open
        
        # --- Update prior session close ---
        # If current bar is the last bar of the overnight session, record its close
        if current_hour == self.overnight_session_end_hour_utc - 1:
            self.prior_session_close = current_close
            self.prior_session_end_bar_index = ctx.bar_index
            self.trade_attempted_today = False # Reset for the new "day" starting after this session
            return None

        # --- Cooldown logic ---
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # --- Exit existing position before US session or on structure ---
        if ctx.has_position():
            if current_hour == self.us_session_start_hour_utc:
                # Close position before US session opens
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_before_us_session_open", "current_hour": current_hour})
            
            # Additional exit logic: If price moves significantly against position
            position = ctx.position
            if position:
                entry_price = position["entry_price"]
                current_price = ctx.bar.close
                move_bps = (current_price - entry_price) / entry_price * 10000

                if position["direction"] == "long" and move_bps < -self.METADATA["declared_sl_bps"] * 0.5: # Half SL as structural exit
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "structural_exit_long_against_us", "entry_price": entry_price, "current_price": current_price, "move_bps": move_bps})
                elif position["direction"] == "short" and move_bps > self.METADATA["declared_sl_bps"] * 0.5:
                    return ctx.signal("flat", confidence=0.7, metadata={"reason": "structural_exit_short_against_us", "entry_price": entry_price, "current_price": current_price, "move_bps": move_bps})
            return None # Hold if no exit condition met

        # --- Entry logic: Only trade during the overnight session ---
        if self.prior_session_close is None or not (self.overnight_session_start_hour_utc <= current_hour < self.overnight_session_end_hour_utc):
            return None
        
        # Ensure we only attempt a trade once per "day" (after the overnight session close is recorded)
        if self.trade_attempted_today and self.prior_session_end_bar_index and ctx.bar_index > self.prior_session_end_bar_index:
            return None

        # Calculate gap from prior session close to current bar open
        gap_bps = (current_open - self.prior_session_close) / self.prior_session_close * 10000

        # Check for significant gap
        if abs(gap_bps) < self.gap_threshold_bps:
            return None

        # Check for continuation (price moving further in gap direction)
        # We look at the current close relative to the open of the current bar
        continuation_bps = (current_close - current_open) / current_open * 10000

        # --- Regime Filters ---
        # 1. Trend Filter (EMA)
        ema_val = self._ema(closes, self.ema_period)
        if ema_val is None:
            return None
        
        # 2. Volatility/Regime Filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"]: # Stand down in extreme volatility
            return None
        
        # 3. Sentiment Filter (Fear & Greed Index)
        fear_greed = ctx.features.get("fear_greed_index", 50)
        # Only trade if not in extreme fear or extreme greed, implying more "normal" conditions for reversion
        if fear_greed < 25 or fear_greed > 75:
            return None

        # --- Gap Reversion Logic ---
        signal_direction: Optional[str] = None
        confidence = 0.0
        reason = ""
        
        # Long opportunity: Price gapped down, and did NOT continue lower significantly
        if gap_bps < -self.gap_threshold_bps and continuation_bps > -self.continuation_threshold_bps:
            # Confirm with RSI if it's oversold
            rsi_val = self._rsi(closes, self.rsi_period)
            if rsi_val is not None and rsi_val < 40: # Not necessarily oversold, but on the lower side
                # Trend confirmation: price below EMA (meaning downtrend, so gap down is against trend, good for reversion)
                if current_close < ema_val:
                    signal_direction = "long"
                    confidence = 0.65 + ctx.rng.uniform(0, 0.1) # Add some randomness
                    reason = "overnight_gap_down_reversion_rsi_low_below_ema"
        
        # Short opportunity: Price gapped up, and did NOT continue higher significantly
        elif gap_bps > self.gap_threshold_bps and continuation_bps < self.continuation_threshold_bps:
            # Confirm with RSI if it's overbought
            rsi_val = self._rsi(closes, self.rsi_period)
            if rsi_val is not None and rsi_val > 60: # Not necessarily overbought, but on the higher side
                # Trend confirmation: price above EMA (meaning uptrend, so gap up is against trend, good for reversion)
                if current_close > ema_val:
                    signal_direction = "short"
                    confidence = 0.65 + ctx.rng.uniform(0, 0.1)
                    reason = "overnight_gap_up_reversion_rsi_high_above_ema"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            self.trade_attempted_today = True
            return ctx.signal(
                signal_direction,
                confidence=min(1.0, confidence), # Cap confidence at 1.0
                metadata={
                    "reason": reason,
                    "gap_bps": round(gap_bps, 2),
                    "continuation_bps": round(continuation_bps, 2),
                    "prior_session_close": round(self.prior_session_close, 2),
                    "current_open": round(current_open, 2),
                    "current_close": round(current_close, 2),
                    "rsi": round(rsi_val, 2) if rsi_val is not None else "N/A",
                    "ema": round(ema_val, 2),
                    "regime": regime,
                    "fear_greed_index": fear_greed,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Log closing details for analysis if needed
        # self.last_trade_bar = ctx.bar_index # Reset cooldown on close, regardless of success
        pass