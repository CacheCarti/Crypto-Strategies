from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class PanicFlushRecovery(Strategy):
    METADATA = {
        "name": "PanicFlushRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # 8 hours
        "warmup_bars": 30,
        "required_features": ["fear_greed_index", "funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_bars = 20
        self.flush_threshold_pct = 2.2
        self.cooldown_bars = 4
        self.max_hold_bars = 16
        self.rsi_period = 14

        self.last_exit_bar = -999
        self.entry_bar = -999
        self.midpoint_target = 0.0

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
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
        self.entry_bar = -999
        self.midpoint_target = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.lookback_bars + self.rsi_period + 2
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)

        if len(closes) < req_bars:
            return None

        current_close = closes[-1]
        prev_high = highs[-2]
        rsi = self._rsi(closes, self.rsi_period)
        rsi_val = rsi if rsi is not None else 50.0

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # ----------------- Position Management & Exits -----------------
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1

            # Exit 1: Target recovery reached (midpoint of flush)
            if self.midpoint_target > 0.0 and current_close >= self.midpoint_target:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_recovery_midpoint_hit",
                        "price": current_close,
                        "midpoint_target": round(self.midpoint_target, 2),
                        "bars_held": bars_held,
                        "rsi": round(rsi_val, 2),
                    },
                )

            # Exit 2: Max duration timeout
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "flush_recovery_max_hold_timeout",
                        "price": current_close,
                        "bars_held": bars_held,
                        "rsi": round(rsi_val, 2),
                    },
                )

            # Exit 3: RSI overbought momentum exhaustion
            if rsi_val >= 68.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "flush_recovery_rsi_overbought",
                        "price": current_close,
                        "rsi": round(rsi_val, 2),
                        "bars_held": bars_held,
                    },
                )

            return None

        # ----------------- Entry Gates & Cooldown -----------------
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Lookback flush evaluation
        window_highs = highs[-self.lookback_bars - 1 : -1]
        window_lows = lows[-self.lookback_bars - 1 : -1]

        peak_high = max(window_highs)
        trough_low = min(window_lows)

        if peak_high <= 0:
            return None

        flush_drop_pct = ((peak_high - trough_low) / peak_high) * 100.0

        # Loosened Entry Conditions:
        # 1. Recent flush drop meets loosened threshold (>= 2.2%)
        # 2. Bullish breakout: close breaks above prior bar's high
        # 3. Not severely overbought (RSI < 60)
        has_flushed = flush_drop_pct >= self.flush_threshold_pct
        capitulation_break = current_close > prev_high
        rsi_open = rsi_val <= 60.0

        if has_flushed and capitulation_break and rsi_open:
            midpoint = (peak_high + trough_low) / 2.0

            self.entry_bar = ctx.bar_index
            self.midpoint_target = midpoint

            # Confidence scaled by sentiment & funding
            confidence = 0.65
            if fear_greed <= 35.0:
                confidence += 0.15
            if funding < 0.0:
                confidence += 0.10
            confidence = min(0.95, max(0.50, confidence))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "panic_flush_capitulation_recovery",
                    "flush_drop_pct": round(flush_drop_pct, 2),
                    "peak_high": round(peak_high, 2),
                    "trough_low": round(trough_low, 2),
                    "midpoint_target": round(midpoint, 2),
                    "prev_high": round(prev_high, 2),
                    "close": round(current_close, 2),
                    "rsi": round(rsi_val, 2),
                    "fear_greed": fear_greed,
                    "funding_rate": funding,
                },
            )

        return None