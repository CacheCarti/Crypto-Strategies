from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackStructural(Strategy):
    METADATA = {
        "name": "EthEmaPullbackStructural",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_p = 20
        self.slow_p = 50
        self.atr_p = 14
        self.cooldown_bars = 4
        self.last_exit_bar = -100

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_p + 15)
        highs = ctx.highs(self.slow_p + 15)
        lows = ctx.lows(self.slow_p + 15)

        if len(closes) < self.slow_p + 5:
            return None

        ema20 = self._ema(closes, self.fast_p)
        ema50 = self._ema(closes, self.slow_p)
        atr_val = self._atr(highs, lows, closes, self.atr_p)

        if ema20 is None or ema50 is None or atr_val is None or atr_val <= 0:
            return None

        # Prior bar values to detect clean reclamation / cross
        prev_closes = closes[:-1]
        prev_ema20 = self._ema(prev_closes, self.fast_p)
        prev_ema50 = self._ema(prev_closes, self.slow_p)

        curr_c = closes[-1]
        curr_l = lows[-1]
        curr_h = highs[-1]
        prev_c = prev_closes[-1]
        prev_l = lows[-2]
        prev_h = highs[-2]

        # Dynamic stop-loss based on structural volatility (ATR-based)
        atr_bps = (atr_val / curr_c) * 10000.0
        dyn_sl = max(140.0, min(320.0, atr_bps * 1.8))
        dyn_tp = max(280.0, min(650.0, dyn_sl * 2.0))

        # Handle active position exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_c < ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_exit_cross_below_ema20",
                        "price": curr_c,
                        "ema20": ema20,
                        "ema50": ema50
                    }
                )
            elif pos_dir == "short" and curr_c > ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_exit_cross_above_ema20",
                        "price": curr_c,
                        "ema20": ema20,
                        "ema50": ema50
                    }
                )
            return None

        # Cooldown guard after trade exit to prevent churn
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Long setup: Uptrend (price & ema20 > ema50), prior bar touched/dipped below EMA20, current bar closes above EMA20
        uptrend = curr_c > ema50 and ema20 > ema50
        bull_pullback_reclaim = (prev_l <= prev_ema20 or prev_c <= prev_ema20) and (curr_c > ema20)

        if uptrend and bull_pullback_reclaim:
            trend_margin = (curr_c - ema50) / curr_c
            conf = min(0.9, max(0.55, 0.6 + trend_margin * 10.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "bullish_ema_pullback_reclaim",
                    "price": curr_c,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps
                }
            )

        # Short setup: Downtrend (price & ema20 < ema50), prior bar touched/exceeded EMA20, current bar closes below EMA20
        downtrend = curr_c < ema50 and ema20 < ema50
        bear_pullback_reject = (prev_h >= prev_ema20 or prev_c >= prev_ema20) and (curr_c < ema20)

        if downtrend and bear_pullback_reject:
            trend_margin = (ema50 - curr_c) / curr_c
            conf = min(0.9, max(0.55, 0.6 + trend_margin * 10.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "bearish_ema_pullback_rejection",
                    "price": curr_c,
                    "ema20": ema20,
                    "ema50": ema50,
                    "atr_bps": atr_bps
                }
            )

        return None