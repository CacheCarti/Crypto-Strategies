from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrashRecoveryBtc(Strategy):
    METADATA = {
        "name": "CrashRecoveryBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 43200,  # 12 hours intended hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.055  # 5.5% drop in 24 bars
        self.cooldown_bars = 12
        self.max_hold_bars = 24
        self.last_exit_bar = -100
        self.entry_bar = -100
        self.target_midpoint = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close

            # Target exit: 24-bar crash midpoint reached
            if self.target_midpoint > 0 and current_close >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.8, metadata={
                    "reason": "flush_midpoint_recovered",
                    "bars_held": bars_held,
                    "target": self.target_midpoint,
                    "price": current_close
                })

            # Time-based hold expiration
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_hold_bars_reached",
                    "bars_held": bars_held,
                    "price": current_close
                })

            return None

        # Cooldown guard after exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate 24-bar rolling return prior to current bar
        p_start = closes[-self.lookback - 1]
        p_prev = closes[-2]
        rolling_ret = (p_prev - p_start) / p_start

        # Check for crash flush: significant drop over last 24 bars
        if rolling_ret > self.flush_threshold_pct:
            return None

        # Strict event boundary: current bar closes ABOVE previous bar's high (capitulation reversal)
        prev_high = highs[-2]
        curr_close = closes[-1]
        if curr_close <= prev_high:
            return None

        # Market stress confirmation: avoid falling knives in non-extreme contexts
        fear_greed = ctx.features.get("fear_greed_index", 50)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        
        # Calculate local range high and low for dynamic midpoint target
        recent_24_high = max(highs[-self.lookback:])
        recent_24_low = min(lows[-self.lookback:])
        midpoint = (recent_24_high + recent_24_low) / 2.0

        # Only enter if the midpoint offers meaningful upside above friction (>60 bps)
        if (midpoint - curr_close) / curr_close < 0.006:
            return None

        self.entry_bar = ctx.bar_index
        self.target_midpoint = midpoint

        # Scale confidence by depth of flush and extreme sentiment
        confidence = 0.70
        if fear_greed < 25:
            confidence += 0.15
        if rolling_ret < -0.08:
            confidence += 0.10
        confidence = min(1.0, confidence)

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=380.0,
            take_profit_bps=550.0,
            horizon_seconds=43200,
            metadata={
                "reason": "crash_flush_capitulation_engulfing",
                "rolling_24h_ret": round(rolling_ret, 4),
                "midpoint_target": round(midpoint, 2),
                "fear_greed": fear_greed,
                "crisis_score": round(crisis_score, 3),
                "price": curr_close,
                "prev_high": prev_high
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_midpoint = 0.0