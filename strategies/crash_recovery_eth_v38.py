from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,  # ~5 hours target recovery window
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_drop: int = 24
        self.cooldown_bars: int = 14
        self.last_exit_bar: int = -999
        self.in_flush: bool = False
        self.flush_peak: float = 0.0
        self.flush_trough: float = 0.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.in_flush = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback_drop + 5)
        highs = ctx.highs(self.lookback_drop + 5)
        lows = ctx.lows(self.lookback_drop + 5)

        if len(closes) < self.lookback_drop + 2:
            return None

        # Manage existing long position exit logic
        if ctx.has_position():
            if self.flush_peak > 0.0 and self.flush_trough > 0.0:
                midpoint = (self.flush_peak + self.flush_trough) / 2.0
                # Take profit near the flush midpoint
                if ctx.bar.close >= midpoint:
                    return ctx.signal(
                        "flat",
                        confidence=0.85,
                        metadata={
                            "reason": "flush_midpoint_reversion_target_hit",
                            "close": ctx.bar.close,
                            "midpoint": midpoint,
                            "flush_trough": self.flush_trough,
                        },
                    )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate to avoid cascading meltdown markets (prior failure mode was buying endless trending meltdowns)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50)
        # Avoid euphoric tops where sharp drops are trend reversals rather than mean-reverting flushes
        if fear_greed > 75:
            return None

        # Calculate rolling 24-bar drop
        peak_24 = max(highs[-self.lookback_drop:])
        curr_close = closes[-1]
        drop_pct = (peak_24 - curr_close) / peak_24

        # Track flush status: 5%+ drop from 24-bar high
        if drop_pct >= 0.050:
            self.in_flush = True
            self.flush_peak = peak_24
            self.flush_trough = min(lows[-6:])

        if not self.in_flush:
            return None

        # Update local trough during flush tracking
        recent_trough = min(lows[-4:])
        if recent_trough < self.flush_trough:
            self.flush_trough = recent_trough

        # Stabilization trigger: current close breaks strictly above previous bar's high
        prev_high = highs[-2]
        is_stabilizing = curr_close > prev_high

        # Relative confirmation: close should be above recent open (green candle)
        is_green = curr_close > ctx.bar.open

        if is_stabilizing and is_green:
            midpoint = (self.flush_peak + self.flush_trough) / 2.0
            # Ensure target still offers positive asymmetry above current price
            expected_gain_bps = ((midpoint - curr_close) / curr_close) * 10000.0

            if expected_gain_bps >= 120.0:
                tp_bps = min(max(expected_gain_bps, 200.0), 500.0)
                sl_bps = 320.0
                confidence = min(0.60 + (drop_pct * 2.0), 0.90)

                # Reset flush state on trigger
                self.in_flush = False

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "flush_stabilization_recovery",
                        "drop_pct": round(drop_pct * 100.0, 2),
                        "prev_high": prev_high,
                        "close": curr_close,
                        "flush_peak": self.flush_peak,
                        "flush_trough": self.flush_trough,
                        "fear_greed": fear_greed,
                    },
                )

        # Cancel flush tracking if stale (price drifts sideways or rises without trigger)
        if drop_pct < 0.025:
            self.in_flush = False

        return None