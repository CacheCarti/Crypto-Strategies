from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackConfirmed(Strategy):
    METADATA = {
        "name": "EthEmaPullbackConfirmed",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 15)
        highs = ctx.highs(self.slow_period + 15)
        lows = ctx.lows(self.slow_period + 15)

        if len(closes) < self.slow_period + 5:
            return None

        # Calculate current and prior EMAs
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None:
            return None

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        price = ctx.bar.close
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Handle active positions: exit on cross against EMA20
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and price < ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_long_ema20_breakdown", "close": price, "ema20": ema20_curr}
                )
            elif direction == "short" and price > ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_short_ema20_breakout", "close": price, "ema20": ema20_curr}
                )
            return None

        # Cooldown guard after trade exit or entry
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Filter crisis / high volatility spikes to avoid fakeouts
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        # Stricter event boundary for Long:
        # 1. Macro trend: EMA20 clearly above EMA50, and EMA50 sloping upward
        # 2. Touch event: Prior bar dipped below EMA20 (low <= ema20_prev)
        # 3. Confirmation trigger: Current bar closes decisively back above EMA20
        long_trend = (ema20_curr > ema50_curr) and (ema50_curr >= ema50_prev)
        long_pullback = prev_low <= (ema20_prev + 0.1 * atr)
        long_reclaim = (prev_close <= ema20_prev * 1.002) and (price > ema20_curr) and (price > ctx.bar.open)

        if long_trend and long_pullback and long_reclaim:
            self.last_entry_bar = ctx.bar_index
            trend_gap_bps = ((ema20_curr - ema50_curr) / ema50_curr) * 10000.0
            confidence = min(0.85, max(0.55, 0.55 + (trend_gap_bps / 300.0) * 0.3))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_ema20_pullback_reclaimed",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "atr": atr,
                    "gap_bps": trend_gap_bps
                }
            )

        # Stricter event boundary for Short:
        # 1. Macro downtrend: EMA20 clearly below EMA50, and EMA50 sloping downward
        # 2. Touch event: Prior bar pierced above EMA20 (high >= ema20_prev)
        # 3. Confirmation trigger: Current bar closes decisively back below EMA20
        short_trend = (ema20_curr < ema50_curr) and (ema50_curr <= ema50_prev)
        short_pullback = prev_high >= (ema20_prev - 0.1 * atr)
        short_reject = (prev_close >= ema20_prev * 0.998) and (price < ema20_curr) and (price < ctx.bar.open)

        if short_trend and short_pullback and short_reject:
            self.last_entry_bar = ctx.bar_index
            trend_gap_bps = ((ema50_curr - ema20_curr) / ema50_curr) * 10000.0
            confidence = min(0.85, max(0.55, 0.55 + (trend_gap_bps / 300.0) * 0.3))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_ema20_pullback_rejected",
                    "price": price,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "atr": atr,
                    "gap_bps": trend_gap_bps
                }
            )

        return None