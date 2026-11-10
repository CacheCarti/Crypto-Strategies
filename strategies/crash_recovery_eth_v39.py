from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # 6 hours average hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.045       # 4.5% drop over 24h window
        self.cooldown_bars = 8          # wait at least 8 bars between trades
        self.max_hold_bars = 16         # time stop to prevent capital trap
        self.last_exit_bar = -100
        self.entry_bar = -1
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
        self.entry_bar = -1
        self.target_midpoint = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 26:
            return None

        # Position management if already in position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
            
            # Exit 1: Reached flush midpoint target
            if self.target_midpoint > 0.0 and ctx.bar.close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "price": ctx.bar.close,
                        "target": self.target_midpoint,
                        "bars_held": bars_held,
                    }
                )
            
            # Exit 2: Time stop if recovery stalls
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "flush_recovery_time_limit_reached",
                        "price": ctx.bar.close,
                        "bars_held": bars_held,
                    }
                )

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush calculation over lookback
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        high_24 = max(window_highs)
        low_24 = min(window_lows)

        # Drop percentage from the peak of the 24-bar window to the low
        drop_pct = (high_24 - low_24) / high_24 if high_24 > 0 else 0.0

        if drop_pct < self.min_drop_pct:
            return None

        # Stabilization condition: current close breaks above previous bar's high
        prev_high = highs[-2]
        curr_close = closes[-1]
        is_stabilized = curr_close > prev_high

        # Ensure current price is still depressed (in lower 40% of the flush range)
        flush_range = high_24 - low_24
        if flush_range <= 0:
            return None

        range_position = (curr_close - low_24) / flush_range

        # Filter: RSI must show extreme oversold relief (< 42)
        rsi = self._rsi(closes, 14)

        if is_stabilized and range_position <= 0.40 and rsi < 42.0:
            # Avoid entries during meltdown / extreme crisis
            market_regime = ctx.market.get("regime", "NORMAL")
            if market_regime == "MELTDOWN":
                return None

            midpoint = low_24 + 0.50 * flush_range
            self.target_midpoint = midpoint
            self.entry_bar = ctx.bar_index

            confidence = min(0.9, 0.55 + (drop_pct * 3.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_stabilization_entry",
                    "drop_pct": round(drop_pct * 100, 2),
                    "range_pos": round(range_position, 2),
                    "rsi": round(rsi, 2),
                    "target_midpoint": round(midpoint, 2),
                    "prev_high": round(prev_high, 2),
                }
            )

        return None