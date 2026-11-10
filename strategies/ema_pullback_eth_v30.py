from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackTrend(Strategy):
    METADATA = {
        "name": "EthEmaPullbackTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 4
        self.last_exit_bar = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14):
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
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 2:
            return None

        highs = ctx.highs(20)
        lows = ctx.lows(20)

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        if ema20 is None or ema50 is None:
            return None

        # Previous bar's EMA20 approximation
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        if ema20_prev is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Manage existing position exits first
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and curr_close < ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_long_ema20_break", "price": curr_close, "ema20": ema20}
                )
            elif direction == "short" and curr_close > ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_short_ema20_break", "price": curr_close, "ema20": ema20}
                )
            return None

        # Respect post-exit cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter out extreme crisis conditions
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Long pullback setup: Uptrend (EMA20 > EMA50 and Price > EMA50)
        # Reclaim condition: dipped below/at EMA20 recently and closed back strictly above EMA20
        dipped_below_ema20_long = (prev_low <= ema20_prev or curr_low <= ema20)
        bullish_reclaim = dipped_below_ema20_long and (curr_close > ema20) and (prev_close <= ema20_prev or curr_close > prev_close)

        if ema20 > ema50 and curr_close > ema50 and bullish_reclaim:
            atr = self._atr(highs, lows, closes, 14) or (curr_close * 0.015)
            trend_margin = (curr_close - ema50) / curr_close
            confidence = min(0.85, max(0.55, 0.55 + trend_margin * 10))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_long_reclaim",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr": atr,
                    "regime": regime
                }
            )

        # Short pullback setup: Downtrend (EMA20 < EMA50 and Price < EMA50)
        # Reclaim condition: spiked above/at EMA20 recently and closed back strictly below EMA20
        spiked_above_ema20_short = (prev_high >= ema20_prev or curr_high >= ema20)
        bearish_rejection = spiked_above_ema20_short and (curr_close < ema20) and (prev_close >= ema20_prev or curr_close < prev_close)

        if ema20 < ema50 and curr_close < ema50 and bearish_rejection:
            atr = self._atr(highs, lows, closes, 14) or (curr_close * 0.015)
            trend_margin = (ema50 - curr_close) / curr_close
            confidence = min(0.85, max(0.55, 0.55 + trend_margin * 10))

            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_pullback_short_rejection",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr": atr,
                    "regime": regime
                }
            )

        return None