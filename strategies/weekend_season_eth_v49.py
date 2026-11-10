from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 900.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold across the weekend
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 24
        self.last_exit_bar = -999
        self.last_entry_week = -1
        self.trend_period = 48
        self.rsi_period = 14

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        # Check existing position management
        if ctx.has_position():
            # Scheduled weekend exit: Sunday late evening (20:00 UTC or later)
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_target_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            # Failsafe weekday exit if hold extended past Sunday night
            if weekday == 0 and hour >= 2:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "post_weekend_failsafe_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Only one weekend attempt per calendar week
        if current_week_id == self.last_entry_week:
            return None

        # Entry Window: Friday evening between 16:00 and 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Regime & filter checks: avoid entering during extreme selloffs/crises
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price-based confirmation: avoid severely overbought or broken structures
        rsi = self._rsi(closes, self.rsi_period)
        sma_fast = self._sma(closes, 20)
        sma_slow = self._sma(closes, self.trend_period)

        if rsi is None or sma_fast is None or sma_slow is None:
            return None

        # Do not buy into an overextended blow-off top
        if rsi > 72.0:
            return None

        # Market trend state alignment
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear" and ctx.bar.close < sma_slow:
            # Skip entries when in confirmed structural bear trend below 48h trend
            return None

        # Scale confidence based on trend alignment and momentum health
        confidence = 0.65
        if ctx.bar.close > sma_fast:
            confidence += 0.15
        if 35.0 <= rsi <= 60.0:
            confidence += 0.10
        confidence = min(1.0, confidence)

        self.last_entry_week = current_week_id
        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "friday_weekend_expansion_long",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "trend_regime": trend_regime,
                "regime": regime,
                "sma_fast": round(sma_fast, 2),
                "sma_slow": round(sma_slow, 2),
                "price": round(ctx.bar.close, 2)
            }
        )