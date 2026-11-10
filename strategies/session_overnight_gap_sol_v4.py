from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class OvernightGapReversionSol(Strategy):
    METADATA = {
        "name": "SOL Overnight Gap Reversion",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Intended hold of around 4 hours
        "warmup_bars": 50,
        "required_features": ["fear_greed_index", "funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.overnight_start_hour = 0  # UTC 00:00
        self.overnight_end_hour = 6    # UTC 06:00
        self.prior_session_close_price = None
        self.last_trade_bar_index = -self.METADATA["warmup_bars"] # Allow trading immediately after warmup
        self.cooldown_bars = 6 # Cooldown for 6 bars (6 hours) after a trade

        # For regime filtering
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.atr_period = 14

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs:
            return None
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < max(self.ema_period_slow, self.atr_period) + 1:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Update prior session close at the start of the overnight window (UTC 00:00)
        # This captures the close of the previous day's trading session (e.g., US session close)
        if current_hour == self.overnight_start_hour and ctx.bar.timestamp.minute == 0:
            if len(closes) > 1:
                self.prior_session_close_price = closes[-2] # Close of the previous bar (end of prior session)
            else:
                self.prior_session_close_price = None # Not enough history
            return None # Don't trade on the first bar of the new "day"

        # Trading logic only within the overnight window (00:00 to 06:00 UTC)
        if self.overnight_start_hour <= current_hour < self.overnight_end_hour:
            if self.prior_session_close_price is None:
                return None

            # Cooldown check
            if ctx.bar_index - self.last_trade_bar_index < self.cooldown_bars:
                return None

            # Regime filters
            # 1. Trend Filter: Only trade gap fades in neutral or opposite trend regimes
            trend_regime = ctx.market.get("trend_regime", "neutral")
            trend_confidence = ctx.market.get("trend_regime_confidence", 0.0)

            # 2. Volatility Filter: Use ATR to gauge market volatility
            atr = self._atr(highs, lows, closes, self.atr_period)
            if atr is None:
                return None
            
            # Simple volatility threshold (e.g., avoid trading if ATR is too low/high relative to price)
            # This is a heuristic, adjust as needed. Here, we normalize ATR by price.
            avg_price = sum(closes[-self.atr_period:]) / self.atr_period
            atr_percent = (atr / avg_price) * 100 if avg_price != 0 else 0
            
            # Avoid trading in extremely low or extremely high volatility (relative to typical)
            # Heuristic thresholds: 0.5% to 3% ATR for 1h SOL bars. Adjust as needed.
            if not (0.5 <= atr_percent <= 2.5):
                return None

            # 3. Sentiment Filter: Avoid trading against strong market sentiment
            fear_greed = ctx.features.get("fear_greed_index", 50)
            
            # Entry logic: Gap Reversion
            gap_threshold_bps = 50 # Minimum gap to consider, in bps
            current_price = ctx.bar.close
            gap_bps = ((current_price - self.prior_session_close_price) / self.prior_session_close_price) * 10000

            signal_direction = None
            reason = ""
            confidence = 0.0
            
            # Long setup: Price gapped down significantly
            if gap_bps < -gap_threshold_bps:
                # Only consider long if trend is not strongly bearish, or if fear_greed is not extreme fear
                if trend_regime != "bear" or trend_confidence < 0.6:
                    if fear_greed > 20: # Avoid extreme fear
                        signal_direction = "long"
                        reason = "overnight_gap_down_reversion"
                        confidence = min(1.0, abs(gap_bps) / 200.0) # Scale confidence by gap size
                        if trend_regime == "bull": confidence *= 1.2
                        if trend_regime == "bear": confidence *= 0.8
                        confidence = max(0.4, min(0.9, confidence)) # Ensure min/max confidence
            
            # Short setup: Price gapped up significantly
            elif gap_bps > gap_threshold_bps:
                # Only consider short if trend is not strongly bullish, or if fear_greed is not extreme greed
                if trend_regime != "bull" or trend_confidence < 0.6:
                    if fear_greed < 80: # Avoid extreme greed
                        signal_direction = "short"
                        reason = "overnight_gap_up_reversion"
                        confidence = min(1.0, abs(gap_bps) / 200.0) # Scale confidence by gap size
                        if trend_regime == "bear": confidence *= 1.2
                        if trend_regime == "bull": confidence *= 0.8
                        confidence = max(0.4, min(0.9, confidence)) # Ensure min/max confidence

            if signal_direction and not ctx.has_position():
                self.last_trade_bar_index = ctx.bar_index
                return ctx.signal(
                    signal_direction,
                    confidence=confidence,
                    metadata={
                        "reason": reason,
                        "current_price": current_price,
                        "prior_session_close": self.prior_session_close_price,
                        "gap_bps": gap_bps,
                        "trend_regime": trend_regime,
                        "trend_confidence": trend_confidence,
                        "atr_percent": atr_percent,
                        "fear_greed_index": fear_greed,
                    }
                )
        
        # Exit logic: If we are outside the overnight window and have a position, consider flattening
        # Or if we reach the end of the overnight window (06:00 UTC)
        if ctx.has_position() and current_hour == self.overnight_end_hour and ctx.bar.timestamp.minute == 0:
            if ctx.position_direction() == "long":
                exit_reason = "overnight_session_end_flat_long"
            else:
                exit_reason = "overnight_session_end_flat_short"
            return ctx.signal("flat", confidence=0.7, metadata={
                "reason": exit_reason,
                "current_price": ctx.bar.close,
                "entry_price": ctx.position["entry_price"]
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adjust internal state based on performance
        pass