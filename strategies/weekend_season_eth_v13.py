from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.entry_bar_idx = -1
        self.last_entered_week = -1

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
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Sunday is 6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_id = iso_year * 100 + iso_week

        sma50 = self._sma(closes, 50)
        rsi = self._rsi(closes, 14)
        if sma50 is None or rsi is None:
            return None

        current_price = ctx.bar.close
        regime = ctx.market.get("regime", "NORMAL")
        fg_index = ctx.features.get("fear_greed_index", 50)

        # In-position logic: check for planned weekend exit
        if ctx.has_position():
            # Planned exit on Sunday between 20:00 and 23:00 UTC
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_target_end_reached",
                        "weekday": weekday,
                        "hour": hour,
                        "price": current_price,
                        "rsi": round(rsi, 2)
                    }
                )
            
            # Momentum breakdown safety exit: drop below SMA50 with oversold flush
            if current_price < sma50 * 0.985 and rsi < 35.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "trend_breakdown_early_cut",
                        "price": current_price,
                        "sma50": round(sma50, 2),
                        "rsi": round(rsi, 2)
                    }
                )

            return None

        # Entry logic: Only on Friday between 18:00 and 23:00 UTC, once per calendar week
        if weekday == 4 and 18 <= hour <= 23:
            if self.last_entered_week == current_week_id:
                return None

            # Skip entries during crisis regime to avoid massive drawdown flushes
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Trend filter: Avoid buying severe secular downtrends
            if current_price < sma50 * 0.96:
                return None

            # Technical trigger: Don't buy severely overbought extremes
            if rsi > 70.0:
                return None

            # Confidence based on trend alignment and sentiment
            confidence = 0.65
            if current_price > sma50:
                confidence += 0.15
            if fg_index > 25:
                confidence += 0.10
            confidence = min(max(confidence, 0.1), 1.0)

            self.last_entered_week = current_week_id
            self.entry_bar_idx = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "friday_evening_weekend_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "sma50": round(sma50, 2),
                    "price": current_price,
                    "fg_index": fg_index
                }
            )

        return None