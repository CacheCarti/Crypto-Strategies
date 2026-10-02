from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolEmaRibbonTrend(Strategy):
    METADATA = {
        "name": "SOL EMA Ribbon Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 75,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.mid_period = 25
        self.slow_period = 60
        self.cooldown_bars = 10
        self.last_exit_bar = -100

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 20)
        if len(closes) < self.slow_period + 10:
            return None

        # Filter out extreme crisis regimes
        if ctx.regime == "crisis" or ctx.market.get("regime", "NORMAL") in ("CRISIS", "MELTDOWN"):
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_mid = self._ema(closes, self.mid_period)
        ema_slow = self._ema(closes, self.slow_period)

        prev_closes = closes[:-1]
        ema_fast_prev = self._ema(prev_closes, self.fast_period)

        if ema_fast is None or ema_mid is None or ema_slow is None or ema_fast_prev is None:
            return None

        close = ctx.bar.close
        spread_bps = (abs(ema_fast - ema_slow) / ema_slow) * 10000.0

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Position management / Exit logic
        if has_pos:
            if pos_dir == "long":
                if ema_fast < ema_mid or close < ema_mid:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_ribbon_breakdown",
                            "close": round(close, 4),
                            "ema_fast": round(ema_fast, 4),
                            "ema_mid": round(ema_mid, 4),
                            "ema_slow": round(ema_slow, 4),
                            "spread_bps": round(spread_bps, 2),
                        },
                    )
            elif pos_dir == "short":
                if ema_fast > ema_mid or close > ema_mid:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_ribbon_breakout",
                            "close": round(close, 4),
                            "ema_fast": round(ema_fast, 4),
                            "ema_mid": round(ema_mid, 4),
                            "ema_slow": round(ema_slow, 4),
                            "spread_bps": round(spread_bps, 2),
                        },
                    )
            return None

        # Hard multi-bar cooldown after exit to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Ribbon minimum expansion filter (at least 45 bps wide)
        min_expansion_bps = 45.0
        if spread_bps < min_expansion_bps:
            return None

        # Scaled confidence based on ribbon expansion
        confidence = min(0.90, max(0.60, 0.60 + (spread_bps / 200.0) * 0.30))

        # Bullish alignment with fast EMA upward slope
        is_bullish = (
            ema_fast > ema_mid > ema_slow
            and close > ema_fast
            and ema_fast > ema_fast_prev
        )

        # Bearish alignment with fast EMA downward slope
        is_bearish = (
            ema_fast < ema_mid < ema_slow
            and close < ema_fast
            and ema_fast < ema_fast_prev
        )

        if is_bullish:
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=680.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "bullish_ema_ribbon_expansion",
                    "close": round(close, 4),
                    "ema_fast": round(ema_fast, 4),
                    "ema_mid": round(ema_mid, 4),
                    "ema_slow": round(ema_slow, 4),
                    "spread_bps": round(spread_bps, 2),
                },
            )

        if is_bearish:
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=680.0,
                horizon_seconds=28800,
                metadata={
                    "reason": "bearish_ema_ribbon_expansion",
                    "close": round(close, 4),
                    "ema_fast": round(ema_fast, 4),
                    "ema_mid": round(ema_mid, 4),
                    "ema_slow": round(ema_slow, 4),
                    "spread_bps": round(spread_bps, 2),
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index