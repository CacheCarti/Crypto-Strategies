from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackStructure(Strategy):
    METADATA = {
        "name": "EthEmaPullbackStructure",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.atr_period = 14
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

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
        closes = ctx.closes(self.slow_period + 5)
        if len(closes) < self.slow_period + 2:
            return None

        highs = ctx.highs(self.atr_period + 5)
        lows = ctx.lows(self.atr_period + 5)

        ema20_curr = self._ema(closes, self.fast_period)
        ema50_curr = self._ema(closes, self.slow_period)
        ema20_prev = self._ema(closes[:-1], self.fast_period)

        if ema20_curr is None or ema50_curr is None or ema20_prev is None:
            return None

        curr_close = ctx.bar.close
        curr_low = ctx.bar.low
        curr_high = ctx.bar.high
        prev_close = closes[-2]
        prev_low = lows[-2] if len(lows) >= 2 else curr_low
        prev_high = highs[-2] if len(highs) >= 2 else curr_high

        atr = self._atr(highs, lows, closes, self.atr_period)
        atr_bps = (atr / curr_close * 10000.0) if (atr and curr_close > 0) else 150.0

        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")

        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Volatility-scaled dynamic structural exit: close back below/above EMA20
            if pos_dir == "long" and curr_close < ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "ema20_loss_exit",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "bars_held": bars_held
                    }
                )
            elif pos_dir == "short" and curr_close > ema20_curr:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "ema20_reclaim_exit",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "bars_held": bars_held
                    }
                )

            # Max duration exit if momentum stalls
            if bars_held >= 12:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_decay_exit", "bars_held": bars_held}
                )
            return None

        # Guard against post-exit chop
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Avoid entries in crisis/meltdown
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Dynamic stop scaled by structural volatility (ATR * 1.8)
        dyn_sl_bps = max(180.0, min(380.0, atr_bps * 1.8))
        dyn_tp_bps = dyn_sl_bps * 2.0

        # Bullish setup: Uptrend (close > EMA50) + Pullback reclamation of EMA20
        # Dipped to/below EMA20 either previous bar or current bar low, but confirmed close above EMA20
        dipped_below_ema20 = (prev_low <= ema20_prev) or (curr_low <= ema20_curr)
        long_reclaim = dipped_below_ema20 and (curr_close > ema20_curr) and (prev_close <= ema20_prev or curr_close > prev_close)

        if curr_close > ema50_curr and ema20_curr > ema50_curr and long_reclaim:
            if trend_regime != "bear":
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=dyn_sl_bps,
                    take_profit_bps=dyn_tp_bps,
                    metadata={
                        "reason": "ema_pullback_reclaim_long",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "ema50": ema50_curr,
                        "atr_bps": atr_bps
                    }
                )

        # Bearish setup: Downtrend (close < EMA50) + Pullback rejection of EMA20
        popped_above_ema20 = (prev_high >= ema20_prev) or (curr_high >= ema20_curr)
        short_rejection = popped_above_ema20 and (curr_close < ema20_curr) and (prev_close >= ema20_prev or curr_close < prev_close)

        if curr_close < ema50_curr and ema20_curr < ema50_curr and short_rejection:
            if trend_regime != "bull":
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=dyn_sl_bps,
                    take_profit_bps=dyn_tp_bps,
                    metadata={
                        "reason": "ema_pullback_reject_short",
                        "close": curr_close,
                        "ema20": ema20_curr,
                        "ema50": ema50_curr,
                        "atr_bps": atr_bps
                    }
                )

        return None