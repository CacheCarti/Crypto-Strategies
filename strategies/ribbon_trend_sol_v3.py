from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolRibbonTrend(Strategy):
    METADATA = {
        "name": "SOL Dynamic EMA Ribbon Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 75,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.mid_period = 25
        self.slow_period = 65
        self.min_spread_pct = 0.009  # Require 0.9% ribbon spread to avoid chop entries
        self.cooldown_bars = 14      # Hard multi-bar cooldown after exit
        self.last_exit_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_period + 20)
        if len(closes) < self.slow_period:
            return None

        # Filter out extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_market_crisis", "regime": market_regime, "price": round(ctx.bar.close, 3)}
                )
            return None

        current_close = ctx.bar.close
        ema_fast = self._ema(closes, self.fast_period)
        ema_mid = self._ema(closes, self.mid_period)
        ema_slow = self._ema(closes, self.slow_period)

        if ema_fast is None or ema_mid is None or ema_slow is None or ema_slow == 0:
            return None

        bullish_alignment = ema_fast > ema_mid > ema_slow
        bearish_alignment = ema_fast < ema_mid < ema_slow

        ribbon_spread = abs(ema_fast - ema_slow) / ema_slow
        confidence = max(0.45, min(0.9, 0.45 + (ribbon_spread / 0.03) * 0.45))

        # Position management: exit when fast crosses back through mid (trend exhaustion)
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and ema_fast < ema_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_bullish_trend_lost",
                        "ema_fast": round(ema_fast, 3),
                        "ema_mid": round(ema_mid, 3),
                        "ema_slow": round(ema_slow, 3),
                        "price": round(current_close, 3),
                    }
                )
            elif pos_dir == "short" and ema_fast > ema_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "exit_bearish_trend_lost",
                        "ema_fast": round(ema_fast, 3),
                        "ema_mid": round(ema_mid, 3),
                        "ema_slow": round(ema_slow, 3),
                        "price": round(current_close, 3),
                    }
                )
            return None

        # Hard cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Filter for sufficient trend expansion
        if ribbon_spread < self.min_spread_pct:
            return None

        # High-conviction long entry
        if bullish_alignment and current_close > ema_fast:
            return ctx.signal(
                "long",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_ribbon_bullish_expansion",
                    "ema_fast": round(ema_fast, 3),
                    "ema_mid": round(ema_mid, 3),
                    "ema_slow": round(ema_slow, 3),
                    "ribbon_spread": round(ribbon_spread, 5),
                    "price": round(current_close, 3),
                }
            )

        # High-conviction short entry
        if bearish_alignment and current_close < ema_fast:
            return ctx.signal(
                "short",
                confidence=round(confidence, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "ema_ribbon_bearish_expansion",
                    "ema_fast": round(ema_fast, 3),
                    "ema_mid": round(ema_mid, 3),
                    "ema_slow": round(ema_slow, 3),
                    "ribbon_spread": round(ribbon_spread, 5),
                    "price": round(current_close, 3),
                }
            )

        return None