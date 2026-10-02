from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicRecoveryETH(Strategy):
    METADATA = {
        "name": "Panic Recovery ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 20
        self.drop_threshold = 0.028  # 2.8% drop over lookback window
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.flush_high = 0.0
        self.flush_low = 0.0
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
        self.flush_high = 0.0
        self.flush_low = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 5:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        prev_high = highs[-2]
        prev_close = closes[-2]
        rsi = self._rsi(closes, period=14)
        fear_greed = ctx.features.get("fear_greed_index", 50)

        # ------------------------------------------------------------------
        # Position Management
        # ------------------------------------------------------------------
        if ctx.has_position():
            # Exit if recovered back to midpoint of the flush
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "flush_midpoint_target_reached",
                    "target_midpoint": round(self.target_midpoint, 2),
                    "close": current_close,
                    "rsi": round(rsi, 2)
                })

            # Overbought momentum exhaustion exit
            if rsi >= 68.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "rsi_overbought_exit",
                    "rsi": round(rsi, 2),
                    "close": current_close
                })

            return None

        # ------------------------------------------------------------------
        # Entry Filters
        # ------------------------------------------------------------------
        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Analyze drop across the rolling lookback window
        window_highs = highs[-self.lookback:]
        window_lows = lows[-self.lookback:]
        max_h = max(window_highs)
        min_l = min(window_lows)

        if max_h <= 0.0:
            return None

        drop_pct = (max_h - min_l) / max_h

        # Stabilization criteria:
        # 1. Minimum 2.8% drop across recent lookback
        # 2. Bullish stabilization bar: closes above previous bar's high or strong green reversal
        # 3. RSI is not already overbought (rsi < 52.0)
        # 4. Market is not in extreme bubble euphoria (fear_greed < 75)
        is_drop = drop_pct >= self.drop_threshold
        is_stabilization = (current_close > prev_high and current_close > current_open) or (
            current_close > prev_close and current_close > (prev_high + lows[-2]) * 0.5 and current_close > current_open
        )

        if is_drop and is_stabilization and rsi < 52.0 and fear_greed < 75:
            self.flush_high = max_h
            self.flush_low = min_l
            # Midpoint target between the flush swing low and swing high
            self.target_midpoint = min_l + (max_h - min_l) * 0.50

            confidence = min(0.85, max(0.55, 0.55 + (drop_pct - self.drop_threshold) * 4.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_recovery_stabilization_entry",
                    "drop_pct": round(drop_pct * 100, 2),
                    "flush_high": round(max_h, 2),
                    "flush_low": round(min_l, 2),
                    "target_midpoint": round(self.target_midpoint, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed
                }
            )

        return None