from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdaptive(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdaptive",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_trade = 0
        self.entry_price = 0.0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_trade = 0
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 5)
        highs = ctx.highs(self.slow_period + 5)
        lows = ctx.lows(self.slow_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        # Compute indicators
        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None or ema50_prev is None:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Manage open position
        if ctx.has_position():
            self.bars_in_trade += 1
            direction = ctx.position_direction()

            # Dynamic exit: Close against EMA20 or time stop
            if direction == "long":
                if curr_close < ema20_curr and prev_close >= ema20_prev:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_ema20_cross_under",
                            "price": curr_close,
                            "ema20": ema20_curr,
                            "bars_held": self.bars_in_trade
                        }
                    )
            elif direction == "short":
                if curr_close > ema20_curr and prev_close <= ema20_prev:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_ema20_cross_over",
                            "price": curr_close,
                            "ema20": ema20_curr,
                            "bars_held": self.bars_in_trade
                        }
                    )

            # Max hold safety exit
            if self.bars_in_trade >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_timeout",
                        "price": curr_close,
                        "bars_held": self.bars_in_trade
                    }
                )

            return None

        # Check cooldown to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.6 or regime == "MELTDOWN":
            return None

        # Dynamic SL/TP based on ATR, bounded to reasonable bps
        atr_bps = (atr / curr_close) * 10000.0
        sl_bps = max(180.0, min(350.0, atr_bps * 1.8))
        tp_bps = max(280.0, min(600.0, atr_bps * 3.0))

        # Long Setup:
        # 1. Clear uptrend: EMA20 > EMA50, price above EMA50
        # 2. Pullback: Current or previous bar dipped to or below EMA20
        # 3. Trigger: Current close reclaims and closes firmly above EMA20
        is_uptrend = ema20_curr > ema50_curr and curr_close > ema50_curr
        pulled_back_long = (prev_low <= ema20_prev or curr_low <= ema20_curr)
        reclaimed_long = curr_close > ema20_curr and prev_close <= ema20_prev * 1.002

        if is_uptrend and pulled_back_long and reclaimed_long:
            self.bars_in_trade = 0
            self.entry_price = curr_close
            trend_strength = (ema20_curr - ema50_curr) / ema50_curr
            confidence = min(0.85, max(0.55, 0.6 + trend_strength * 20.0))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "long_pullback_ema20_reclaim",
                    "price": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "atr_bps": atr_bps,
                    "regime": regime
                }
            )

        # Short Setup:
        # 1. Clear downtrend: EMA20 < EMA50, price below EMA50
        # 2. Pullback: Current or previous bar rose to or above EMA20
        # 3. Trigger: Current close rejects and closes firmly below EMA20
        is_downtrend = ema20_curr < ema50_curr and curr_close < ema50_curr
        pulled_back_short = (prev_high >= ema20_prev or curr_high >= ema20_curr)
        reclaimed_short = curr_close < ema20_curr and prev_close >= ema20_prev * 0.998

        if is_downtrend and pulled_back_short and reclaimed_short:
            self.bars_in_trade = 0
            self.entry_price = curr_close
            trend_strength = (ema50_curr - ema20_curr) / ema50_curr
            confidence = min(0.85, max(0.55, 0.6 + trend_strength * 20.0))

            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "short_pullback_ema20_rejection",
                    "price": curr_close,
                    "ema20": ema20_curr,
                    "ema50": ema50_curr,
                    "atr_bps": atr_bps,
                    "regime": regime
                }
            )

        return None