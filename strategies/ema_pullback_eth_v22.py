from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackReversal(Strategy):
    METADATA = {
        "name": "EthEmaPullbackReversal",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        closes = ctx.closes(self.slow_period + 5)
        highs = ctx.highs(self.slow_period + 5)
        lows = ctx.lows(self.slow_period + 5)

        if len(closes) < self.slow_period + 2:
            return None

        # Calculate current and prior indicators
        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)
        ema50_prev = self._ema(closes[:-1], self.slow_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema20 is None or ema50 is None or ema20_prev is None or ema50_prev is None or atr is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        prev_low = lows[-2]
        curr_high = highs[-1]
        prev_high = highs[-2]

        has_pos = ctx.has_position()
        direction = ctx.position_direction()

        # Dynamic stop loss and take profit based on ATR bps
        atr_bps = (atr / curr_close) * 10000.0
        sl_bps = max(180.0, min(350.0, atr_bps * 1.5))
        tp_bps = max(360.0, min(700.0, atr_bps * 3.0))

        # Check exits on open positions
        if has_pos:
            if direction == "long" and curr_close < ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_long_ema20_cross_under",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema50": ema50
                })
            elif direction == "short" and curr_close > ema20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "exit_short_ema20_cross_over",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema50": ema50
                })
            return None

        # Enforce strict cooldown between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries during crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
            return None

        # Long Entry: Bullish trend (EMA20 > EMA50, price > EMA50)
        # Event: Prior bar touched or pierced below EMA20, current bar reclaims & closes cleanly above EMA20
        uptrend = ema20 > ema50 and curr_close > ema50
        bull_pullback_trigger = (prev_low <= ema20_prev or prev_close < ema20_prev) and (curr_close > ema20)

        if uptrend and bull_pullback_trigger:
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "ema_pullback_reclaim_long",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps
                }
            )

        # Short Entry: Bearish trend (EMA20 < EMA50, price < EMA50)
        # Event: Prior bar touched or pierced above EMA20, current bar closes cleanly back below EMA20
        downtrend = ema20 < ema50 and curr_close < ema50
        bear_pullback_trigger = (prev_high >= ema20_prev or prev_close > ema20_prev) and (curr_close < ema20)

        if downtrend and bear_pullback_trigger:
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=14400,
                metadata={
                    "reason": "ema_pullback_reject_short",
                    "price": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps
                }
            )

        return None