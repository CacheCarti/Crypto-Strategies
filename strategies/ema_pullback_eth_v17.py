from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdvanced",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_bars = 6
        self.last_exit_bar = -999

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(70)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(70)
        lows = ctx.lows(70)

        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        atr14 = self._atr(highs, lows, closes, 14)

        if ema20 is None or ema50 is None or atr14 is None:
            return None

        curr_close = closes[-1]
        prev_close = closes[-2]
        curr_low = lows[-1]
        prev_low = lows[-2]
        curr_high = highs[-1]
        prev_high = highs[-2]

        # Exit logic for open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and curr_close < ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_long_cross_below_ema20", "close": curr_close, "ema20": ema20}
                )
            elif pos_dir == "short" and curr_close > ema20:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "exit_short_cross_above_ema20", "close": curr_close, "ema20": ema20}
                )
            return None

        # Cooldown guard after exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid meltdowns and extreme crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.7:
            return None

        # Long pullback setup:
        # 1. Clear uptrend: EMA20 > EMA50
        # 2. Pullback test: previous or current low dipped down to or below EMA20
        # 3. Confirmation: current close is firmly above EMA20 and close > open
        is_uptrend = ema20 > ema50
        dipped_below_ema20_long = (prev_low <= ema20 * 1.001) or (curr_low <= ema20 * 1.001)
        reclaimed_ema20_long = (curr_close > ema20) and (curr_close > prev_close) and (curr_close > ctx.bar.open)

        if is_uptrend and dipped_below_ema20_long and reclaimed_ema20_long:
            trend_gap_bps = ((ema20 - ema50) / ema50) * 10000.0
            confidence = min(0.9, max(0.5, 0.5 + (trend_gap_bps / 200.0) * 0.2))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "ema_pullback_reclaim_long",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "trend_gap_bps": trend_gap_bps,
                    "atr": atr14
                }
            )

        # Short pullback setup:
        # 1. Clear downtrend: EMA20 < EMA50
        # 2. Pullback test: previous or current high touched or exceeded EMA20
        # 3. Confirmation: current close is firmly below EMA20 and close < open
        is_downtrend = ema20 < ema50
        touched_ema20_short = (prev_high >= ema20 * 0.999) or (curr_high >= ema20 * 0.999)
        rejected_ema20_short = (curr_close < ema20) and (curr_close < prev_close) and (curr_close < ctx.bar.open)

        if is_downtrend and touched_ema20_short and rejected_ema20_short:
            trend_gap_bps = ((ema50 - ema20) / ema50) * 10000.0
            confidence = min(0.9, max(0.5, 0.5 + (trend_gap_bps / 200.0) * 0.2))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "ema_pullback_reject_short",
                    "close": curr_close,
                    "ema20": ema20,
                    "ema50": ema50,
                    "trend_gap_bps": trend_gap_bps,
                    "atr": atr14
                }
            )

        return None