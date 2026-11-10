from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold (multi-day weekend hold)
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_period = 50
        self.last_exit_bar = -100
        self.cooldown_bars = 24  # Wait at least 24 hours between trades
        self.entry_bar = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
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
        closes = ctx.closes(self.warmup_period + 5)
        if len(closes) < self.warmup_period:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Sunday is 6, Friday is 4
        hour = ts.hour
        bar_idx = ctx.bar_index

        # Position exit logic: Exit late Sunday (Sunday 20:00 - 23:00 UTC)
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit signal on Sunday evening
                if weekday == 6 and hour >= 20:
                    self.last_exit_bar = bar_idx
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "weekend_target_window_ended",
                            "hour": hour,
                            "weekday": weekday,
                            "price": ctx.bar.close,
                            "bars_held": bar_idx - self.entry_bar if self.entry_bar > 0 else 0
                        }
                    )
            return None

        # Cooldown guard: must have rested since previous exit
        if bar_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market stress gate: Avoid entering during extreme crisis regimes
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        # Seasonality window: Friday evening (18:00 to 23:00 UTC)
        is_friday_evening = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_evening:
            return None

        # Indicators to filter out deeply broken downward momentum
        ema_slow = self._ema(closes, 50)
        rsi = self._rsi(closes, 14)

        if ema_slow is None or rsi is None:
            return None

        # Price setup:
        # Require ETH not to be in complete freefall (RSI > 35) nor excessively overbought (RSI < 68)
        # and price reasonably close to or above the 50-hour EMA (no more than 3% below EMA50)
        price = ctx.bar.close
        if rsi < 35.0 or rsi > 68.0:
            return None

        if price < ema_slow * 0.97:
            return None

        # Sizing / Confidence logic
        base_confidence = 0.65
        if price >= ema_slow:
            base_confidence += 0.15
        if 42.0 <= rsi <= 58.0:
            base_confidence += 0.10

        confidence = min(max(base_confidence, 0.5), 0.95)

        self.entry_bar = bar_idx
        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=450.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_evening_seasonality_long",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "ema50": round(ema_slow, 2),
                "price": round(price, 2),
                "regime": regime
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1