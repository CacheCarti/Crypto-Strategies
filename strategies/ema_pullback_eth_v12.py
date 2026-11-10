from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackEvent(Strategy):
    METADATA = {
        "name": "EthEmaPullbackEvent",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours avg swing hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_p = 20
        self.slow_p = 50
        self.atr_p = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -100

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
        closes = ctx.closes(70)
        highs = ctx.highs(70)
        lows = ctx.lows(70)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Calculate Indicators
        ema20 = self._ema(closes, self.fast_p)
        ema50 = self._ema(closes, self.slow_p)
        atr = self._atr(highs, lows, closes, self.atr_p)

        if ema20 is None or ema50 is None or atr is None:
            return None

        prev_closes = closes[:-1]
        prev_ema20 = self._ema(prev_closes, self.fast_p)
        if prev_ema20 is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        curr_high = highs[-1]

        # Manage open position exits via rule: cross against EMA20
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_long_cross_below_ema20", "close": curr_close, "ema20": ema20}
                )
            elif pos_dir == "short" and curr_close > ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "exit_short_cross_above_ema20", "close": curr_close, "ema20": ema20}
                )
            return None

        # Mandatory Cooldown Gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid entries in extreme crisis regime
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic Stop & Target based on ATR (in basis points)
        atr_bps = (atr / curr_close) * 10000.0
        stop_bps = max(150.0, min(350.0, atr_bps * 1.5))
        tp_bps = stop_bps * 2.0

        # Event: Bullish Trend & Pullback Reclaim
        # 1. Macro Trend: Price & EMA20 comfortably above EMA50
        # 2. Event Reclaim: Low pierced or touched EMA20, but Close strictly crossed back above EMA20
        if ema20 > ema50 and curr_close > ema50:
            if curr_low <= ema20 and curr_close > ema20 and prev_close <= prev_ema20:
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=28800,
                    metadata={
                        "reason": "ema_pullback_reclaim_bull",
                        "close": curr_close,
                        "ema20": ema20,
                        "ema50": ema50,
                        "atr_bps": atr_bps
                    }
                )

        # Event: Bearish Trend & Pullback Reclaim
        # 1. Macro Trend: Price & EMA20 comfortably below EMA50
        # 2. Event Rejection: High pierced or touched EMA20, but Close strictly crossed back below EMA20
        if ema20 < ema50 and curr_close < ema50:
            if curr_high >= ema20 and curr_close < ema20 and prev_close >= prev_ema20:
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=stop_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=28800,
                    metadata={
                        "reason": "ema_pullback_rejection_bear",
                        "close": curr_close,
                        "ema20": ema20,
                        "ema50": ema50,
                        "atr_bps": atr_bps
                    }
                )

        return None