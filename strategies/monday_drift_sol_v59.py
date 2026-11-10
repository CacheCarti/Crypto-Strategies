from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 55,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 16
        self.bars_in_position = 0

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14):
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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        price = ctx.bar.close

        ema_trend = self._ema(closes, 48)
        rsi = self._rsi(closes, 14)

        if ema_trend is None or rsi is None:
            return None

        has_pos = ctx.has_position()
        if has_pos:
            self.bars_in_position += 1

            # Exit criteria: Tuesday morning (UTC >= 0) or late Monday drift complete
            exit_by_time = (weekday == 0 and hour >= 23) or (weekday == 1 and hour >= 2)
            exit_by_overheat = rsi > 78.0
            max_hold_hit = self.bars_in_position >= 30

            if exit_by_time or exit_by_overheat or max_hold_hit:
                reason = "monday_window_close" if exit_by_time else ("rsi_overbought" if exit_by_overheat else "max_hold_reached")
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "bars_held": self.bars_in_position,
                        "price": price,
                    }
                )
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: Sunday late UTC (18:00 - 23:00) or early Monday Asian session (00:00 - 05:00)
        is_sunday_window = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 5)

        if not (is_sunday_window or is_monday_early):
            return None

        # Technical filter: avoid severe downtrends and heavily overbought conditions
        trend_aligned = price >= (ema_trend * 0.975)
        momentum_valid = 32.0 < rsi < 65.0

        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime_safe = crisis_score < 0.65

        if trend_aligned and momentum_valid and regime_safe:
            # Dynamic confidence based on relative strength to trend
            conf = 0.65
            if price > ema_trend:
                conf += 0.15
            if rsi < 50.0:
                conf += 0.10

            return ctx.signal(
                "long",
                confidence=min(conf, 0.95),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_monday_seasonality_rebound",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "ema_trend": round(ema_trend, 2),
                    "crisis_score": round(crisis_score, 2),
                    "price": price,
                }
            )

        return None