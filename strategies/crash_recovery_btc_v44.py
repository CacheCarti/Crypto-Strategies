from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicFlushRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # 6 hours target hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.045         # Realistic 4.5% 24h flush to guarantee ~20-80 events
        self.cooldown_bars = 8            # Strict post-exit cooldown to avoid overtrading
        self.max_hold_bars = 10           # Dynamic time-stop to lock gains / free capital
        self.last_exit_bar = -100
        self.entry_bar_idx = -100
        self.target_midpoint = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
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
        current_bar = ctx.bar_index

        # Position Management: Take profit at midpoint of flush or time-based horizon
        if ctx.has_position():
            bars_held = current_bar - self.entry_bar_idx
            if self.target_midpoint > 0.0 and current_close >= self.target_midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "target_midpoint": self.target_midpoint,
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "time_based_flush_horizon_exit",
                        "close": current_close,
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown guard: prevent chained entries after rapid stops/exits
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        # 1. Measure 24-bar high and drop magnitude
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        drop_pct = (window_high - current_close) / window_high

        # 2. Capitulation reversal confirmation: close > previous bar's high
        prev_high = highs[-2]
        is_capitulation_engulf = (current_close > prev_high) and (closes[-2] <= prev_high)

        # 3. Oversold context check
        rsi = self._rsi(closes, 14)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Entry condition: true flush + confirmed reversal candle + washed-out RSI
        if drop_pct >= self.min_drop_pct and is_capitulation_engulf:
            if rsi is not None and rsi <= 45.0:
                self.target_midpoint = (window_high + window_low) / 2.0
                self.entry_bar_idx = current_bar

                confidence = 0.70
                if fear_greed < 30.0:
                    confidence = 0.85

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "capitulation_candle_rebound_above_prev_high",
                        "drop_pct": round(drop_pct * 100, 2),
                        "rsi": round(rsi, 2),
                        "fear_greed": fear_greed,
                        "target_midpoint": round(self.target_midpoint, 2),
                        "window_high": round(window_high, 2)
                    }
                )

        return None