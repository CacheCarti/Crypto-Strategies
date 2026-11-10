from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackReversal(Strategy):
    METADATA = {
        "name": "EthEmaPullbackReversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_trade_bar = -100

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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(70)
        highs = ctx.highs(70)
        lows = ctx.lows(70)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Check market regime to avoid entering during crisis/meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime"})
            return None

        # Compute Indicators
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        atr_val = self._atr(highs, lows, closes, self.atr_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None or atr_val is None:
            return None

        curr_close = closes[-1]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        current_pos = ctx.position_direction()

        # Dynamic Stop Loss / Take Profit derived from ATR
        atr_bps = (atr_val / curr_close) * 10000.0
        sl_bps = max(180.0, min(350.0, atr_bps * 1.5))
        tp_bps = max(350.0, min(750.0, atr_bps * 3.0))

        # Position Management: Exit logic based on EMA20 cross against position
        if current_pos == "long":
            if curr_close < ema20_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_long_ema20_cross",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "bar_index": ctx.bar_index,
                    },
                )
            return None

        if current_pos == "short":
            if curr_close > ema20_curr:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "exit_short_ema20_cross",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "bar_index": ctx.bar_index,
                    },
                )
            return None

        # Cooldown guard: prevent overtrading
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Uptrend definition: EMA20 above EMA50, price above EMA50
        bull_trend = (ema20_curr > ema50_curr) and (curr_close > ema50_curr)
        # Pullback & Reclaim: previous or current bar tested below EMA20, but current bar closes back above EMA20
        pullback_tested_bull = (prev_low <= ema20_prev) or (curr_low <= ema20_curr)
        bull_reclaim = (curr_close > ema20_curr) and (prev_close <= ema20_prev or curr_close > prev_close)

        if bull_trend and pullback_tested_bull and bull_reclaim:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "ema_pullback_bull_reclaim",
                    "close": curr_close,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "atr_bps": round(atr_bps, 1),
                },
            )

        # Downtrend definition: EMA20 below EMA50, price below EMA50
        bear_trend = (ema20_curr < ema50_curr) and (curr_close < ema50_curr)
        # Pullback & Reject: previous or current bar tested above EMA20, but current bar closes back below EMA20
        pullback_tested_bear = (prev_high >= ema20_prev) or (curr_high >= ema20_curr)
        bear_reject = (curr_close < ema20_curr) and (prev_close >= ema20_prev or curr_close < prev_close)

        if bear_trend and pullback_tested_bear and bear_reject:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "ema_pullback_bear_reject",
                    "close": curr_close,
                    "ema20": round(ema20_curr, 2),
                    "ema50": round(ema50_curr, 2),
                    "atr_bps": round(atr_bps, 1),
                },
            )

        return None