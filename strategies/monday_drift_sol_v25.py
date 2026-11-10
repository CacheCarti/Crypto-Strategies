from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.last_entry_bar = -999
        self.entry_price = 0.0

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
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        current_time = ctx.bar.timestamp
        weekday = current_time.weekday()  # Monday is 0, Sunday is 6
        hour = current_time.hour
        price = ctx.bar.close

        # Handle active position management and scheduled exits
        if ctx.has_position():
            # Planned seasonal exit: late Monday night / early Tuesday
            # Mon 20:00 UTC through Tue 02:00 UTC
            is_mon_close = (weekday == 0 and hour >= 21)
            is_tue_open = (weekday == 1 and hour <= 2)

            if is_mon_close or is_tue_open:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "monday_drift_window_close",
                        "weekday": weekday,
                        "hour": hour,
                        "exit_price": price
                    }
                )
            return None

        # Mandatory cooldown after an exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Guard: At most one trade per weekly window
        if ctx.bar_index - self.last_entry_bar < 72:
            return None

        # Seasonal Entry Window: Sunday late UTC (18:00 - 23:00 UTC)
        in_entry_window = (weekday == 6 and hour >= 18)
        if not in_entry_window:
            return None

        # Market Context Filters: Avoid crisis and extreme downward momentum
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.6:
            return None

        # Indicators
        rsi = self._rsi(closes, period=14)
        ema_trend = self._ema(closes, period=36)
        if rsi is None or ema_trend is None:
            return None

        # High-conviction entry conditions:
        # Not extremely overbought entering the drift window, and price holding above or near trend
        trend_ratio = price / ema_trend
        if rsi < 62.0 and trend_ratio > 0.96:
            fng = ctx.features.get("fear_greed_index", 50.0)
            
            # Confidence scaling
            confidence = 0.70
            if rsi < 45.0:
                confidence += 0.10
            if trend_ratio >= 1.0:
                confidence += 0.10

            self.last_entry_bar = ctx.bar_index
            self.entry_price = price

            return ctx.signal(
                "long",
                confidence=min(0.95, confidence),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sunday_late_drift_accumulation",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "trend_ratio": round(trend_ratio, 4),
                    "fear_greed": fng,
                    "entry_price": price
                }
            )

        return None