from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEmaRibbonTrend(Strategy):
    METADATA = {
        "name": "SolEmaRibbonTrend",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 850.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_fast = 9
        self.period_mid = 21
        self.period_slow = 55
        self.period_rsi = 14
        self.cooldown_bars = 12
        self.min_spread_bps = 65.0
        self.last_exit_bar = -999
        self.prev_bullish = False
        self.prev_bearish = False
        self.prev_ema_slow = None

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed_bars = self.period_slow + 5
        closes = ctx.closes(needed_bars)
        if len(closes) < needed_bars:
            return None

        # Filter out extreme crisis regimes to prevent whipsaws
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN") or ctx.regime == "crisis":
            return None

        ema_fast = self._ema(closes, self.period_fast)
        ema_mid = self._ema(closes, self.period_mid)
        ema_slow = self._ema(closes, self.period_slow)
        rsi = self._rsi(closes, self.period_rsi)

        if ema_fast is None or ema_mid is None or ema_slow is None or rsi is None:
            return None

        current_close = ctx.bar.close
        spread_bps = (abs(ema_fast - ema_slow) / current_close) * 10000.0

        # Slow trend slope filter
        slow_slope_up = self.prev_ema_slow is not None and ema_slow >= self.prev_ema_slow
        slow_slope_down = self.prev_ema_slow is not None and ema_slow <= self.prev_ema_slow

        bullish_aligned = (ema_fast > ema_mid > ema_slow) and (current_close > ema_mid)
        bearish_aligned = (ema_fast < ema_mid < ema_slow) and (current_close < ema_mid)

        in_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Robust Exit logic: exit on EMA crossover breaks, avoiding single-bar wick noise
        if in_pos:
            should_exit = False
            exit_reason = ""
            if pos_dir == "long" and ema_fast < ema_mid:
                should_exit = True
                exit_reason = "ribbon_bearish_cross"
            elif pos_dir == "short" and ema_fast > ema_mid:
                should_exit = True
                exit_reason = "ribbon_bullish_cross"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                self.prev_bullish = bullish_aligned
                self.prev_bearish = bearish_aligned
                self.prev_ema_slow = ema_slow
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "ema_fast": round(ema_fast, 2),
                        "ema_mid": round(ema_mid, 2),
                        "ema_slow": round(ema_slow, 2),
                        "rsi": round(rsi, 2),
                        "close": round(current_close, 2),
                    }
                )

        # Hard Cooldown check after position exit
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_exit <= self.cooldown_bars or in_pos:
            self.prev_bullish = bullish_aligned
            self.prev_bearish = bearish_aligned
            self.prev_ema_slow = ema_slow
            return None

        # Entry threshold checks: require alignment transition and sufficient expansion
        is_fresh_bull = bullish_aligned and not self.prev_bullish
        is_fresh_bear = bearish_aligned and not self.prev_bearish

        if spread_bps < self.min_spread_bps:
            self.prev_bullish = bullish_aligned
            self.prev_bearish = bearish_aligned
            self.prev_ema_slow = ema_slow
            return None

        # Long Entry: Fresh alignment transition + expanding spread + healthy RSI + rising slow EMA
        if is_fresh_bull and slow_slope_up and (46.0 <= rsi <= 68.0):
            norm_spread = min(max((spread_bps - self.min_spread_bps) / 120.0, 0.0), 1.0)
            confidence = round(0.60 + 0.30 * norm_spread, 2)

            self.prev_bullish = bullish_aligned
            self.prev_bearish = bearish_aligned
            self.prev_ema_slow = ema_slow
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_ribbon_expansion_fresh",
                    "spread_bps": round(spread_bps, 1),
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_mid": round(ema_mid, 2),
                    "ema_slow": round(ema_slow, 2),
                    "close": round(current_close, 2),
                }
            )

        # Short Entry: Fresh alignment transition + expanding spread + healthy RSI + falling slow EMA
        if is_fresh_bear and slow_slope_down and (32.0 <= rsi <= 54.0):
            norm_spread = min(max((spread_bps - self.min_spread_bps) / 120.0, 0.0), 1.0)
            confidence = round(0.60 + 0.30 * norm_spread, 2)

            self.prev_bullish = bullish_aligned
            self.prev_bearish = bearish_aligned
            self.prev_ema_slow = ema_slow
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_ribbon_expansion_fresh",
                    "spread_bps": round(spread_bps, 1),
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_mid": round(ema_mid, 2),
                    "ema_slow": round(ema_slow, 2),
                    "close": round(current_close, 2),
                }
            )

        self.prev_bullish = bullish_aligned
        self.prev_bearish = bearish_aligned
        self.prev_ema_slow = ema_slow
        return None