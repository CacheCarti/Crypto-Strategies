from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEmaRibbonTrend(Strategy):
    METADATA = {
        "name": "SOL EMA Ribbon Dynamic Trend",
        "domain": "sol_usdc",
        "declared_sl_bps": 360.0,
        "declared_tp_bps": 800.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_period = 10
        self.mid_period = 25
        self.slow_period = 60
        self.min_spread_pct = 0.80
        self.cooldown_bars = 14
        self.last_exit_bar = -999

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
        closes = ctx.closes(self.slow_period + 20)
        if len(closes) < self.slow_period + 10:
            return None

        # Filter extreme crisis regimes to avoid whipsaw regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        ema_fast = self._ema(closes, self.fast_period)
        ema_mid = self._ema(closes, self.mid_period)
        ema_slow = self._ema(closes, self.slow_period)
        rsi = self._rsi(closes, 14)

        if ema_fast is None or ema_mid is None or ema_slow is None or rsi is None:
            return None

        price = ctx.bar.close
        ribbon_spread_pct = ((ema_fast - ema_slow) / ema_slow) * 100.0
        abs_spread_pct = abs(ribbon_spread_pct)

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic: smooth exit only when the fast EMA crosses the mid EMA to avoid wick whipsaws
        if has_pos:
            if pos_dir == "long" and ema_fast <= ema_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_ribbon_cross_bearish",
                        "price": price,
                        "ema_fast": ema_fast,
                        "ema_mid": ema_mid,
                        "ema_slow": ema_slow,
                        "rsi": rsi,
                        "spread_pct": ribbon_spread_pct,
                    },
                )
            elif pos_dir == "short" and ema_fast >= ema_mid:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_ribbon_cross_bullish",
                        "price": price,
                        "ema_fast": ema_fast,
                        "ema_mid": ema_mid,
                        "ema_slow": ema_slow,
                        "rsi": rsi,
                        "spread_pct": ribbon_spread_pct,
                    },
                )
            return None

        # Hard cooldown after every exit to prevent rapid-fire re-entries
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_exit < self.cooldown_bars:
            return None

        # Minimum expansion threshold ensures we only enter strong distinct trends
        if abs_spread_pct < self.min_spread_pct:
            return None

        # Robust alignment + momentum confirmation filters
        bull_aligned = (ema_fast > ema_mid > ema_slow) and (price > ema_fast) and (52.0 < rsi < 75.0)
        bear_aligned = (ema_fast < ema_mid < ema_slow) and (price < ema_fast) and (25.0 < rsi < 48.0)

        # Scale confidence with ribbon expansion magnitude
        conf = min(0.95, max(0.55, 0.50 + (abs_spread_pct / 4.0) * 0.45))

        if bull_aligned:
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_ribbon_expansion_confirmed",
                    "price": price,
                    "ema_fast": ema_fast,
                    "ema_mid": ema_mid,
                    "ema_slow": ema_slow,
                    "rsi": rsi,
                    "spread_pct": ribbon_spread_pct,
                    "bars_since_exit": bars_since_exit,
                },
            )

        if bear_aligned:
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_ribbon_expansion_confirmed",
                    "price": price,
                    "ema_fast": ema_fast,
                    "ema_mid": ema_mid,
                    "ema_slow": ema_slow,
                    "rsi": rsi,
                    "spread_pct": ribbon_spread_pct,
                    "bars_since_exit": bars_since_exit,
                },
            )

        return None