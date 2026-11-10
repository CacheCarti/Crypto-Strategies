from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 129600,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        weekday = current_time.weekday()
        hour = current_time.hour
        price = ctx.bar.close

        ema24 = self._ema(closes, 24)
        rsi14 = self._rsi(closes, 14)
        if ema24 is None or rsi14 is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        has_pos = ctx.has_position()

        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0

            # Sunday afternoon/evening UTC exit
            is_sunday_exit = (weekday == 6 and hour >= 18)
            # Monday early morning safety exit
            is_monday_exit = (weekday == 0 and hour >= 2)
            # Technical overextended take-profit exit
            is_exhaustion = rsi14 >= 74.0

            if is_sunday_exit or is_monday_exit or is_exhaustion:
                reason = "weekend_window_expired" if (is_sunday_exit or is_monday_exit) else "rsi_exhaustion_exit"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi14, 2),
                        "price": price,
                    }
                )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis avoidance
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Seasonality Entry Window: Friday 16:00 to Saturday 04:00 UTC
        is_friday_window = (weekday == 4 and hour >= 16)
        is_saturday_window = (weekday == 5 and hour <= 4)
        seasonality_trigger = is_friday_window or is_saturday_window

        if not seasonality_trigger:
            return None

        # Price-action filters:
        # Not heavily in a bear dump, not severely overbought, ETH supported above/near EMA24
        price_support = price >= (ema24 * 0.985)
        healthy_momentum = 35.0 <= rsi14 <= 66.0
        no_hard_bear = trend_regime != "bear"

        if price_support and healthy_momentum and no_hard_bear:
            confidence = 0.65
            if price > ema24:
                confidence += 0.10
            if 42.0 <= rsi14 <= 58.0:
                confidence += 0.10

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(round(confidence, 2), 1.0),
                stop_loss_bps=400.0,
                take_profit_bps=650.0,
                horizon_seconds=129600,
                metadata={
                    "reason": "friday_weekend_accumulation_flow",
                    "weekday": weekday,
                    "hour": hour,
                    "price": price,
                    "ema24": round(ema24, 2),
                    "rsi": round(rsi14, 2),
                    "trend_regime": trend_regime,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index