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
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_week = -1
        self.bars_in_trade = 0
        self.cooldown_until_bar = 0

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
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        has_pos = ctx.has_position()
        price = ctx.bar.close

        # Position tracking and dynamic calendar exits
        if has_pos:
            self.bars_in_trade += 1
            # Exit conditions: Tuesday morning UTC (hour >= 4) or Monday post-US close (hour >= 22)
            should_exit = False
            exit_reason = ""
            if weekday == 0 and hour >= 23:
                should_exit = True
                exit_reason = "monday_session_end"
            elif weekday == 1 and hour >= 2:
                should_exit = True
                exit_reason = "tuesday_dawn_cutoff"
            elif self.bars_in_trade > 30:
                should_exit = True
                exit_reason = "max_holding_bars_reached"

            if should_exit:
                self.cooldown_until_bar = ctx.bar_index + 12
                self.bars_in_trade = 0
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": exit_reason,
                        "bars_held": self.bars_in_trade,
                        "price": price
                    }
                )
            return None

        # Reset holding counter when flat
        self.bars_in_trade = 0

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Maximum 1 seasonality trade per calendar week to prevent multi-firing / overtrading
        if self.last_traded_week == iso_week:
            return None

        # Execution window: Sunday 18:00 UTC through Monday 06:00 UTC
        in_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 6)
        if not in_entry_window:
            return None

        # Regime & Macro Filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Technical confirmation: avoid buying into extreme downtrend blow-offs
        ema20 = self._ema(closes, 20)
        rsi = self._rsi(closes, 14)
        if ema20 is None or rsi is None:
            return None

        # Confirm price is not in freefall (RSI > 32) and not overbought (RSI < 65)
        # Price should be within 3% of EMA20 to avoid catching catastrophic knives
        price_to_ema = price / ema20
        if 32.0 <= rsi <= 65.0 and 0.965 <= price_to_ema <= 1.035:
            self.last_traded_week = iso_week
            confidence = 0.65 if rsi < 50 else 0.55

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=550.0,
                horizon_seconds=86400,
                metadata={
                    "reason": "sunday_monday_seasonality_turnaround",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "price": price,
                    "ema20": round(ema20, 2),
                    "price_to_ema": round(price_to_ema, 4)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_in_trade = 0
        self.cooldown_until_bar = ctx.bar_index + 8