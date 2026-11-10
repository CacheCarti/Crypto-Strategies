from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthEmaPullbackAdvanced(Strategy):
    METADATA = {
        "name": "EthEmaPullbackAdvanced",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 20
        self.slow_period = 50
        self.cooldown_period = 6
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
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
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

        # Check market regime guard: avoid opening in outright meltdown or extreme crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.70 and not ctx.has_position():
            return None

        # Calculate Indicators
        ema20 = self._ema(closes, self.fast_period)
        ema50 = self._ema(closes, self.slow_period)
        atr14 = self._atr(highs, lows, closes, 14)

        if ema20 is None or ema50 is None or atr14 is None:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        prev_low = lows[-2]
        prev_high = highs[-2]

        # Exit management for open positions
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit when price closes back below EMA20
                if current_close < ema20:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "long_exit_closed_below_ema20",
                            "close": current_close,
                            "ema20": ema20,
                            "ema50": ema50
                        }
                    )
            elif pos_dir == "short":
                # Exit when price closes back above EMA20
                if current_close > ema20:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "short_exit_closed_above_ema20",
                            "close": current_close,
                            "ema20": ema20,
                            "ema50": ema50
                        }
                    )
            return None

        # Entry logic: mandatory cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Dynamic SL based on ATR to prevent premature trailing-stop giveback
        atr_bps = (atr14 / current_close) * 10000.0
        sl_bps = max(160.0, min(350.0, atr_bps * 1.5))
        tp_bps = sl_bps * 2.0

        # Long Setup:
        # 1. Macro Trend: EMA20 > EMA50 AND Close > EMA50
        # 2. Pullback: Previous bar dipped to touch/pierce EMA20 (prev_low <= ema20 or prev_close <= ema20)
        # 3. Confirmation: Current bar reclaims and closes firmly above EMA20 (current_close > ema20)
        if ema20 > ema50 and current_close > ema50:
            if (prev_low <= ema20 or prev_close <= ema20) and current_close > ema20:
                confidence = 0.70
                # Quality bump if trend regime is confirmed bull
                if ctx.market.get("trend_regime", "neutral") == "bull":
                    confidence = 0.85
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "long_ema_pullback_reclaim",
                        "close": current_close,
                        "prev_low": prev_low,
                        "ema20": ema20,
                        "ema50": ema50,
                        "atr14": atr14
                    }
                )

        # Short Setup:
        # 1. Macro Trend: EMA20 < EMA50 AND Close < EMA50
        # 2. Pullback: Previous bar rallied to touch/pierce EMA20 (prev_high >= ema20 or prev_close >= ema20)
        # 3. Confirmation: Current bar rejects and closes firmly below EMA20 (current_close < ema20)
        if ema20 < ema50 and current_close < ema50:
            if (prev_high >= ema20 or prev_close >= ema20) and current_close < ema20:
                confidence = 0.70
                if ctx.market.get("trend_regime", "neutral") == "bear":
                    confidence = 0.85
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "short_ema_pullback_rejection",
                        "close": current_close,
                        "prev_high": prev_high,
                        "ema20": ema20,
                        "ema50": ema50,
                        "atr14": atr14
                    }
                )

        return None