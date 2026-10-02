from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolEmaRibbonTrend(Strategy):
    METADATA = {
        "name": "SOL EMA Ribbon Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 75,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.mid_period = 25
        self.slow_period = 60
        self.rsi_period = 14
        self.min_spread_bps = 50.0
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.prev_bull_aligned = False
        self.prev_bear_aligned = False

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 30)
        if len(closes) < self.slow_period + 10:
            return None

        # Filter out extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_mid = self._ema(closes, self.mid_period)
        ema_slow = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, self.rsi_period)

        if ema_fast is None or ema_mid is None or ema_slow is None or rsi is None:
            return None

        current_close = ctx.bar.close
        bull_aligned = (ema_fast > ema_mid) and (ema_mid > ema_slow)
        bear_aligned = (ema_fast < ema_mid) and (ema_mid < ema_slow)

        spread_bps = (abs(ema_fast - ema_slow) / current_close) * 10000.0
        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic: Exit only on definitive ribbon breakdown
        if has_pos:
            if pos_dir == "long" and (ema_fast < ema_mid or current_close < ema_slow):
                self.last_exit_bar = ctx.bar_index
                self.prev_bull_aligned = bull_aligned
                self.prev_bear_aligned = bear_aligned
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_ribbon_break",
                        "ema_fast": round(ema_fast, 3),
                        "ema_mid": round(ema_mid, 3),
                        "ema_slow": round(ema_slow, 3),
                        "spread_bps": round(spread_bps, 2),
                        "rsi": round(rsi, 2),
                    },
                )
            elif pos_dir == "short" and (ema_fast > ema_mid or current_close > ema_slow):
                self.last_exit_bar = ctx.bar_index
                self.prev_bull_aligned = bull_aligned
                self.prev_bear_aligned = bear_aligned
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_ribbon_break",
                        "ema_fast": round(ema_fast, 3),
                        "ema_mid": round(ema_mid, 3),
                        "ema_slow": round(ema_slow, 3),
                        "spread_bps": round(spread_bps, 2),
                        "rsi": round(rsi, 2),
                    },
                )

            self.prev_bull_aligned = bull_aligned
            self.prev_bear_aligned = bear_aligned
            return None

        # Hard Cooldown Guard
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_exit < self.cooldown_bars:
            self.prev_bull_aligned = bull_aligned
            self.prev_bear_aligned = bear_aligned
            return None

        # Strict fresh transition triggers to avoid repeated continuous firing
        fresh_bull = bull_aligned and not self.prev_bull_aligned
        fresh_bear = bear_aligned and not self.prev_bear_aligned

        signal = None
        if fresh_bull and spread_bps >= self.min_spread_bps:
            if current_close > ema_fast and 45.0 <= rsi <= 72.0:
                confidence = min(0.90, max(0.60, 0.55 + (spread_bps / 200.0) * 0.25))
                signal = ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fresh_bull_ribbon_expansion",
                        "ema_fast": round(ema_fast, 3),
                        "ema_mid": round(ema_mid, 3),
                        "ema_slow": round(ema_slow, 3),
                        "spread_bps": round(spread_bps, 2),
                        "rsi": round(rsi, 2),
                        "price": current_close,
                    },
                )
        elif fresh_bear and spread_bps >= self.min_spread_bps:
            if current_close < ema_fast and 28.0 <= rsi <= 55.0:
                confidence = min(0.90, max(0.60, 0.55 + (spread_bps / 200.0) * 0.25))
                signal = ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "fresh_bear_ribbon_expansion",
                        "ema_fast": round(ema_fast, 3),
                        "ema_mid": round(ema_mid, 3),
                        "ema_slow": round(ema_slow, 3),
                        "spread_bps": round(spread_bps, 2),
                        "rsi": round(rsi, 2),
                        "price": current_close,
                    },
                )

        self.prev_bull_aligned = bull_aligned
        self.prev_bear_aligned = bear_aligned
        return signal