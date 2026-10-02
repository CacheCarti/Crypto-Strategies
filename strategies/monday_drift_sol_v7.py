from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolSundaySeasonality(Strategy):
    METADATA = {
        "name": "SolSundaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 64800,  # ~18 hours
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.ema_period = 28
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.last_traded_week = -1
        self.bars_in_pos = 0

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

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(values[:period]) / period
        for v in values[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(45)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # 0=Mon, 1=Tue, ..., 6=Sun
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        week_key = iso_year * 100 + iso_week

        rsi = self._rsi(closes, self.rsi_period)
        ema = self._ema(closes, self.ema_period)
        if rsi is None or ema is None:
            return None

        has_pos = ctx.has_position()

        # Handle Open Position Exits
        if has_pos:
            self.bars_in_pos += 1
            
            # Time-based Seasonality Exit: Monday evening (>=21 UTC) or Tuesday morning (>=0 UTC)
            is_monday_night = (weekday == 0 and hour >= 21)
            is_tuesday = (weekday == 1)
            is_overextended = rsi > 74.0

            if is_monday_night or is_tuesday or is_overextended or self.bars_in_pos >= 24:
                reason = "seasonality_window_closed"
                if is_overextended:
                    reason = "rsi_overextended_take_profit"
                elif self.bars_in_pos >= 24:
                    reason = "max_hold_duration_reached"

                self.last_exit_bar = ctx.bar_index
                self.bars_in_pos = 0
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema": round(ema, 2),
                        "price": round(current_price, 2),
                        "bars_held": self.bars_in_pos,
                    }
                )
            return None

        # Reset in-position bar counter when flat
        self.bars_in_pos = 0

        # Enforce cooldown and single-trade-per-week gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.last_traded_week == week_key:
            return None

        # Check Market Regime Filter
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime == "MELTDOWN" or crisis_score > 0.65:
            return None

        # Entry Window: Sunday late UTC (18-23) or Early Monday (00-03)
        is_sunday_entry = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 3)

        if is_sunday_entry or is_monday_early:
            # Technical Confirmation:
            # 1. Price is not in freefall meltdown (within 4.5% of EMA28)
            # 2. RSI is healthy/bouncing (between 32 and 62)
            within_trend_band = current_price >= (ema * 0.955)
            rsi_valid = 32.0 <= rsi <= 62.0

            if within_trend_band and rsi_valid:
                # Higher confidence when RSI is recovering from mild oversold
                confidence = 0.65
                if 38.0 <= rsi <= 52.0:
                    confidence = 0.80

                self.last_traded_week = week_key
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "sunday_monday_seasonality_long",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema": round(ema, 2),
                        "price": round(current_price, 2),
                        "regime": regime,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.bars_in_pos = 0