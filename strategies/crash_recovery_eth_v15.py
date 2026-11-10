from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicRecovery(Strategy):
    METADATA = {
        "name": "EthPanicRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 320.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_midpoint = 0.0

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
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_price = ctx.bar.close

        # Position Management & Mean Reversion Exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Midpoint target reached or time stop
            if self.target_midpoint > 0 and current_price >= self.target_midpoint:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "price": current_price,
                    "target": self.target_midpoint,
                    "bars_held": bars_held
                })
            if bars_held >= 12:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "panic_recovery_time_exit",
                    "price": current_price,
                    "bars_held": bars_held
                })
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush Detection over the last 24 bars
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        flush_depth_pct = (recent_high - recent_low) / recent_high if recent_high > 0 else 0.0
        drop_from_high = (recent_high - closes[-2]) / recent_high if recent_high > 0 else 0.0

        # State requirement: Flush depth at least 4.5% to avoid micro-dips while capturing real flushes
        is_panic_flush = flush_depth_pct >= 0.045 and drop_from_high >= 0.040

        if not is_panic_flush:
            return None

        # Stabilization trigger: Current bar closes above prior bar's high
        prior_high = highs[-2]
        is_stabilized = current_price > prior_high

        # Relative strength / oversold confirmation to avoid buying deep falling knives prematurely
        rsi_val = self._rsi(closes, period=14)
        rsi_valid = rsi_val <= 42.0

        # Avoid meltdown or extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime_ok = crisis_score < 0.85

        if is_stabilized and rsi_valid and regime_ok:
            # Recovery target: 50% retracement of the flush range
            self.target_midpoint = recent_low + (recent_high - recent_low) * 0.50
            self.entry_bar = ctx.bar_index

            confidence = min(0.9, max(0.6, 0.6 + flush_depth_pct * 3.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=320.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "flush_stabilization_long",
                    "flush_depth_pct": round(flush_depth_pct * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "recent_high": recent_high,
                    "recent_low": recent_low,
                    "target_midpoint": round(self.target_midpoint, 2),
                    "price": current_price
                }
            )

        return None