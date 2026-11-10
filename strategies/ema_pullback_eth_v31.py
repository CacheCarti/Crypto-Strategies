from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackStructural(Strategy):
    METADATA = {
        "name": "EthEmaPullbackStructural",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 21600,  # ~6 hours target swing
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_p = 20
        self.slow_p = 50
        self.atr_p = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(self.slow_p + 5)
        highs = ctx.highs(self.slow_p + 5)
        lows = ctx.lows(self.slow_p + 5)

        if len(closes) < self.slow_p + 2:
            return None

        ema20 = self._ema(closes, self.fast_p)
        ema50 = self._ema(closes, self.slow_p)
        atr = self._atr(highs, lows, closes, self.atr_p)

        if ema20 is None or ema50 is None or atr is None or atr <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        prev_low = lows[-2]
        curr_high = highs[-1]
        prev_high = highs[-2]

        # In-position dynamic management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Structural exit: candle closes decisively across EMA20 against trend
            if pos_dir == "long" and curr_close < ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_cross_below_ema20", "close": curr_close, "ema20": ema20, "bars_held": bars_held}
                )
            elif pos_dir == "short" and curr_close > ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_cross_above_ema20", "close": curr_close, "ema20": ema20, "bars_held": bars_held}
                )

            # Volatility-based time guard (timed exit if stalled > 12 bars)
            if bars_held >= 12:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_decay_exit", "close": curr_close, "bars_held": bars_held}
                )

            return None

        # Post-exit cooldown gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime filter: avoid taking pullbacks during extreme stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volatility-scaled dynamic stops (ATR-based structural floor/ceiling)
        atr_bps = (atr / curr_close) * 10000.0
        dyn_sl = max(180.0, min(420.0, atr_bps * 1.8))
        dyn_tp = max(300.0, min(800.0, dyn_sl * 2.0))

        # Long Setup:
        # 1. Structural Uptrend: EMA20 > EMA50 and Price > EMA50
        # 2. Pullback trigger: prior bar dipped into or below EMA20, current bar reclaims & closes above EMA20
        uptrend = (ema20 > ema50) and (curr_close > ema50)
        long_pullback = (prev_low <= ema20 or prev_close <= ema20) and (curr_close > ema20) and (curr_close > prev_close)

        if uptrend and long_pullback:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "ema_pullback_reclaim_long",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps,
                    "dyn_sl": dyn_sl,
                    "dyn_tp": dyn_tp
                }
            )

        # Short Setup:
        # 1. Structural Downtrend: EMA20 < EMA50 and Price < EMA50
        # 2. Pullback trigger: prior bar spiked into or above EMA20, current bar rejects & closes below EMA20
        downtrend = (ema20 < ema50) and (curr_close < ema50)
        short_pullback = (prev_high >= ema20 or prev_close >= ema20) and (curr_close < ema20) and (curr_close < prev_close)

        if downtrend and short_pullback:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "ema_pullback_rejection_short",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps,
                    "dyn_sl": dyn_sl,
                    "dyn_tp": dyn_tp
                }
            )

        return None