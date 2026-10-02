from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicRecoveryETH(Strategy):
    METADATA = {
        "name": "Panic Recovery ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 320.0,
        "declared_hold_seconds": 7200,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.018  # 1.8% drop threshold over lookback window
        self.cooldown_bars = 6
        self.last_exit_bar = -999
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
        opens = ctx.opens(self.lookback + 5)

        if len(closes) < self.lookback + 2:
            return None

        current_close = closes[-1]
        current_open = opens[-1]
        prev_close = closes[-2]

        # Manage open position exit (take profit at midpoint of the flush range)
        if ctx.has_position():
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "target_midpoint": round(self.target_midpoint, 2),
                        "close": round(current_close, 2),
                    },
                )
            return None

        # Cooldown guard after closing a trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate high-low drop over the flush lookback window
        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        flush_high_val = max(recent_highs)
        flush_low_val = min(recent_lows)

        if flush_high_val <= 0.0:
            return None

        drop_pct = (flush_high_val - flush_low_val) / flush_high_val
        midpoint = flush_low_val + (flush_high_val - flush_low_val) * 0.50

        # Stabilization criteria:
        # 1. Meaningful drop (>= 1.8%)
        # 2. Bullish stabilization bar (green candle and closes above previous close)
        # 3. Price is still in the lower half of the flush range (room to run to midpoint)
        is_green_reversal = (current_close > current_open) and (current_close > prev_close)
        below_midpoint = current_close < midpoint

        if drop_pct >= self.min_drop_pct and is_green_reversal and below_midpoint:
            rsi_val = self._rsi(closes, 14)
            fear_greed = ctx.features.get("fear_greed_index", 50.0)

            self.target_midpoint = midpoint

            # Confidence scaling based on depth of flush and oversold conditions
            base_conf = 0.65
            if drop_pct >= 0.03:
                base_conf += 0.10
            if rsi_val < 35.0:
                base_conf += 0.10
            confidence = min(0.90, base_conf)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_recovery_stabilization_entry",
                    "drop_pct": round(drop_pct * 100, 2),
                    "flush_high": round(flush_high_val, 2),
                    "flush_low": round(flush_low_val, 2),
                    "target_midpoint": round(midpoint, 2),
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fear_greed,
                    "close": round(current_close, 2),
                },
            )

        return None