from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRebound(Strategy):
    METADATA = {
        "name": "SolPanicRebound",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.065       # 6.5% to ensure sufficient quality events
        self.cooldown_bars = 6          # mandatory cooldown between exits and new entries
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.panic_low = 0.0
        self.target_price = 0.0

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low

        # Manage existing long position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar

            # Midpoint take profit or stop below panic low
            if current_close >= self.target_price and self.target_price > 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_rebound_target", "target": self.target_price, "close": current_close}
                )

            if current_close < self.panic_low and self.panic_low > 0.0:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "violated_panic_low", "panic_low": self.panic_low, "close": current_close}
                )

            # Time stop to free capital and prevent multi-day bleed
            if bars_held >= 10:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_hold_time_reached", "bars_held": bars_held}
                )

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Panic calculation over the last 24 bars
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        peak_high = max(window_highs)
        trough_low = min(window_lows)

        if peak_high <= 0.0:
            return None

        drawdown = (peak_high - current_close) / peak_high

        # Look for stabilization bar closing above previous bar's high
        prev_bar_high = highs[-2]
        is_stabilization = (current_close > prev_bar_high) and (current_close > ctx.bar.open)

        # RSI confirmation to avoid early knife-catching in protracted trends
        rsi_val = self._rsi(closes, period=14)

        if drawdown >= self.min_drop_pct and is_stabilization and rsi_val < 42.0:
            self.entry_bar = ctx.bar_index
            self.panic_low = trough_low
            # Target is flush range midpoint
            midpoint = (peak_high + trough_low) / 2.0
            self.target_price = midpoint

            # Compute risk parameters in bps
            risk_dist = max(current_close - self.panic_low, current_close * 0.015)
            sl_bps = min(max((risk_dist / current_close) * 10000.0, 250.0), 550.0)
            tp_bps = min(max(((midpoint - current_close) / current_close) * 10000.0, 300.0), 750.0)

            fear_greed = ctx.features.get("fear_greed_index", 50.0)
            confidence = 0.70 if fear_greed < 40 else 0.60

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=36000,
                metadata={
                    "reason": "sol_panic_stabilization_rebound",
                    "drawdown_pct": round(drawdown * 100, 2),
                    "rsi": round(rsi_val, 1),
                    "panic_low": self.panic_low,
                    "target_midpoint": self.target_price,
                    "fear_greed": fear_greed,
                }
            )

        return None