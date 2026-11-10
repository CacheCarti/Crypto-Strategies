from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.050  # 5% drop over 24 bars (~500 bps drop)
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 16
        self.midpoint_target = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        bar_idx = ctx.bar_index

        # Position management
        if ctx.has_position():
            bars_in_trade = bar_idx - self.entry_bar
            
            # Midpoint target exit or time-based exhaustion
            if current_close >= self.midpoint_target and self.midpoint_target > 0.0:
                self.last_exit_bar = bar_idx
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "flush_midpoint_target_hit",
                    "target": round(self.midpoint_target, 2),
                    "close": round(current_close, 2),
                    "bars_held": bars_in_trade
                })

            if bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = bar_idx
                return ctx.signal("flat", confidence=0.6, metadata={
                    "reason": "max_hold_horizon_reached",
                    "close": round(current_close, 2),
                    "bars_held": bars_in_trade
                })

            return None

        # Cooldown guard
        if bar_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute 24-bar drop
        ref_close = closes[-self.lookback]
        rolling_return = (current_close - ref_close) / ref_close

        # Require a sharp drop of at least 5% over 24 bars
        if rolling_return > self.flush_threshold_pct:
            return None

        # Capitulation reversal trigger: current close > previous bar high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # Technical filter: RSI oversold confirmation to avoid false bounces in mid-trend
        rsi_val = self._rsi(closes, 14)
        if rsi_val > 42.0:
            return None

        # Optional sentiment sanity: not firing during extreme greed
        fgi = ctx.features.get("fear_greed_index", 50)
        if fgi > 60:
            return None

        # Dynamic midpoint calculation
        recent_high_24 = max(highs[-self.lookback:])
        recent_low_24 = min(lows[-self.lookback:])
        self.midpoint_target = recent_low_24 + (recent_high_24 - recent_low_24) * 0.50

        # Confidence scaled by depth of crash and oversold severity
        drop_intensity = min(abs(rolling_return) / 0.10, 1.0)
        confidence = round(max(0.60, min(0.95, 0.50 + drop_intensity * 0.40)), 2)

        self.entry_bar = bar_idx

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "capitulation_engulfing_reversal",
                "drop_24h_pct": round(rolling_return * 100, 2),
                "rsi": round(rsi_val, 2),
                "fear_greed": fgi,
                "midpoint_target": round(self.midpoint_target, 2),
                "prev_high": round(prev_high, 2),
                "close": round(current_close, 2)
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index