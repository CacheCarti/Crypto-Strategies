from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicReversal(Strategy):
    METADATA = {
        "name": "SolPanicReversal",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold for swing snapback
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold = 0.065     # 6.5% drop over 24 bars (~24h on 1h bars)
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.panic_low = 0.0
        self.target_midpoint = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.panic_low = 0.0
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = 0.0, 0.0
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses -= diff
        if losses == 0:
            return 100.0
        rs = (gains / period) / (losses / period)
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_price = ctx.bar.close

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar

            # Take profit near the flush-range midpoint
            if self.target_midpoint > 0 and current_price >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_flush_midpoint", "price": current_price, "target": self.target_midpoint}
                )

            # Emergency stop under the structural panic low
            if self.panic_low > 0 and current_price < self.panic_low:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "violated_panic_low", "price": current_price, "low": self.panic_low}
                )

            # Time stop / exhaustion check
            if bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_decay_exit", "bars_held": bars_held, "price": current_price}
                )

            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate flush across the rolling 24-bar window
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        peak_to_trough = (recent_high - recent_low) / recent_high

        # 24-bar cumulative return
        start_price = closes[-self.lookback]
        rolling_return = (current_price - start_price) / start_price

        # Must have flushed >= 6.5% peak-to-trough or rolling return
        flush_detected = (peak_to_trough >= self.drop_threshold) or (rolling_return <= -self.drop_threshold)
        if not flush_detected:
            return None

        # Stabilization confirmation: close above previous bar's high
        prev_high = highs[-2]
        is_stabilization = ctx.bar.close > prev_high

        if not is_stabilization:
            return None

        # RSI filter: Ensure market reached true distress, but is curling up
        rsi_val = self._rsi(closes, period=14)
        if rsi_val > 48.0:
            return None

        # Avoid entering during absolute meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Compute dynamic TP & SL levels
        midpoint = recent_low + (recent_high - recent_low) * 0.50
        dist_to_midpoint_bps = ((midpoint - current_price) / current_price) * 10000.0

        # Discard if target upside is smaller than friction + risk margin
        if dist_to_midpoint_bps < 120.0:
            return None

        self.panic_low = recent_low
        self.target_midpoint = midpoint
        self.entry_bar = ctx.bar_index

        stop_loss_bps = min(max(((current_price - recent_low) / current_price) * 10000.0, 180.0), 450.0)
        take_profit_bps = min(dist_to_midpoint_bps, 600.0)

        confidence = 0.75
        if rsi_val < 32.0:
            confidence += 0.15

        return ctx.signal(
            "long",
            confidence=round(confidence, 2),
            stop_loss_bps=round(stop_loss_bps, 1),
            take_profit_bps=round(take_profit_bps, 1),
            horizon_seconds=28800,
            metadata={
                "reason": "sol_panic_stabilization_rebound",
                "drop_pct": round(peak_to_trough * 100, 2),
                "rsi": round(rsi_val, 2),
                "panic_low": round(recent_low, 2),
                "target_midpoint": round(midpoint, 2),
                "price": round(current_price, 2),
            }
        )