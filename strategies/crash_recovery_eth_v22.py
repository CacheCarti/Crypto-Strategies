from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,  # ~5 hours average hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 24
        self.flush_threshold_pct = -0.045  # -4.5% over 24h
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
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
        closes = ctx.closes(self.lookback_bars + 2)
        highs = ctx.highs(self.lookback_bars + 2)
        lows = ctx.lows(self.lookback_bars + 2)

        if len(closes) < self.lookback_bars + 1:
            return None

        current_close = ctx.bar.close
        current_idx = ctx.bar_index

        # Position Management: Take profit near recovery midpoint
        if ctx.has_position():
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "target_midpoint": self.target_midpoint,
                        "close": current_close,
                    },
                )
            return None

        # Cooldown guard after trade exit
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate 24-bar rolling flush
        ref_high = max(highs[-self.lookback_bars:])
        ref_low = min(lows[-self.lookback_bars:])
        drop_from_high = (current_close - ref_high) / ref_high

        # Primary Condition: Significant drop over 24 bars
        if drop_from_high > self.flush_threshold_pct:
            return None

        # Stabilization Trigger: Current close breaks above previous bar high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # Indicator confirmation: Deep oversold or turning momentum (RSI)
        rsi = self._rsi(closes, period=14)
        if rsi is None or rsi > 46.0:
            return None

        # Macro risk filter: Avoid complete liquidity meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.85:
            return None

        # Calculate midpoint of the flush range for target
        self.target_midpoint = (ref_high + ref_low) / 2.0
        dist_to_mid_bps = ((self.target_midpoint - current_close) / current_close) * 10000.0

        # Ensure realistic risk-reward (target at least 150 bps away)
        if dist_to_mid_bps < 150.0:
            return None

        confidence = min(0.90, max(0.55, abs(drop_from_high) * 12.0))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=280.0,
            take_profit_bps=min(450.0, max(220.0, dist_to_mid_bps)),
            horizon_seconds=18000,
            metadata={
                "reason": "panic_flush_stabilization_rebound",
                "drop_pct": round(drop_from_high * 100, 2),
                "rsi": round(rsi, 2),
                "ref_high": ref_high,
                "ref_low": ref_low,
                "target_midpoint": round(self.target_midpoint, 2),
                "prev_high": prev_high,
            },
        )