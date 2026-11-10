from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackTrend(Strategy):
    METADATA = {
        "name": "EthEmaPullbackTrend",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        closes = ctx.closes(70)
        highs = ctx.highs(70)
        lows = ctx.lows(70)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Calculate indicators
        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        prev_ema20 = self._ema(closes[:-1], self.fast_period)
        prev_ema50 = self._ema(closes[:-1], self.slow_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if not (ema20 and ema50 and prev_ema20 and prev_ema50 and atr):
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        pos_dir = ctx.position_direction()

        # Exit logic when position is active: EMA20 cross against position
        if ctx.has_position():
            if pos_dir == "long" and curr_close < ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_ema20_break_down",
                        "close": curr_close,
                        "ema20": ema20,
                        "bars_held": ctx.bar_index - self.last_exit_bar
                    }
                )
            elif pos_dir == "short" and curr_close > ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_ema20_break_up",
                        "close": curr_close,
                        "ema20": ema20,
                        "bars_held": ctx.bar_index - self.last_exit_bar
                    }
                )
            return None

        # Cooldown guard after last exit to prevent overtrading and fee drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid choppy/crisis states
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Long Setup:
        # 1. Clear uptrend: EMA20 > EMA50 and curr_close > EMA50
        # 2. Pullback trigger: Previous bar dipped to or below EMA20 (low <= prev_ema20 or close < prev_ema20),
        #    and current bar reclaims EMA20 with a close above it.
        bullish_trend = (ema20 > ema50) and (curr_close > ema50)
        pullback_reclaim_long = (prev_low <= prev_ema20 or prev_close <= prev_ema20) and (curr_close > ema20)

        if bullish_trend and pullback_reclaim_long:
            # Dynamic SL/TP based on ATR, bounded by realistic bps limits
            atr_bps = (atr / curr_close) * 10000.0
            sl_bps = max(150.0, min(300.0, atr_bps * 1.5))
            tp_bps = max(280.0, min(500.0, atr_bps * 2.8))

            confidence = 0.75 if curr_close > prev_close else 0.65

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "uptrend_ema20_pullback_reclaim",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps
                }
            )

        # Short Setup:
        # 1. Clear downtrend: EMA20 < EMA50 and curr_close < EMA50
        # 2. Pullback trigger: Previous bar popped to or above EMA20 (high >= prev_ema20 or close >= prev_ema20),
        #    and current bar rejects EMA20 with a close below it.
        bearish_trend = (ema20 < ema50) and (curr_close < ema50)
        pullback_reject_short = (prev_high >= prev_ema20 or prev_close >= prev_ema20) and (curr_close < ema20)

        if bearish_trend and pullback_reject_short:
            atr_bps = (atr / curr_close) * 10000.0
            sl_bps = max(150.0, min(300.0, atr_bps * 1.5))
            tp_bps = max(280.0, min(500.0, atr_bps * 2.8))

            confidence = 0.75 if curr_close < prev_close else 0.65

            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "downtrend_ema20_pullback_reject",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps
                }
            )

        return None