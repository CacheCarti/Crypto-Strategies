from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 64800,  # ~18 hours hold through Monday
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_week = -1
        self.cooldown_until_bar = 0
        self.entry_bar = 0

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # 0=Monday, 6=Sunday
        hour = ts.hour
        # ISO calendar (year, week_num, weekday)
        current_year, current_week, _ = ts.isocalendar()
        week_key = current_year * 100 + current_week

        # Check existing position management
        if ctx.has_position():
            # Exit Window: Monday 20:00 UTC through end of Monday or Tuesday 00:00
            # Also exit if held past 24 bars (~24 hours on 1h)
            bars_held = ctx.bar_index - self.entry_bar
            should_exit = False
            exit_reason = ""

            if (weekday == 0 and hour >= 21) or weekday == 1:
                should_exit = True
                exit_reason = "monday_session_close"
            elif bars_held >= 22:
                should_exit = True
                exit_reason = "max_holding_reached"

            if should_exit:
                self.cooldown_until_bar = ctx.bar_index + 12
                rsi_val = self._rsi(closes, 14) or 50.0
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "bars_held": bars_held,
                        "rsi": round(rsi_val, 2),
                        "price": ctx.bar.close,
                        "hour": hour,
                        "weekday": weekday
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Entry Window: Late Sunday (UTC 18:00 - 23:00) or very early Monday (UTC 00:00 - 03:00)
        in_entry_window = (weekday == 6 and hour >= 18) or (weekday == 0 and hour <= 3)
        if not in_entry_window:
            return None

        # Gate to exactly 1 seasonal trade execution per calendar week
        if self.last_traded_week == week_key:
            return None

        # Regime & Macro safety checks
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.60:
            return None

        # Technical Price Trigger & Filter
        # 1. SOL should not be completely collapsing (price relative to SMA20 or RSI not ultra oversold/broken)
        sma20 = self._sma(closes, 20)
        rsi = self._rsi(closes, 14)
        if sma20 is None or rsi is None:
            return None

        # Avoid entering if momentum is crashing violently into the weekend (e.g., RSI < 25)
        # Also avoid extreme blowoff tops (RSI > 75)
        if rsi < 25.0 or rsi > 74.0:
            return None

        # Macro cross-asset confirmation: BTC 24h return shouldn't be in a steep freefall
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret < -6.0:
            return None

        # Dynamic confidence based on trend alignment
        confidence = 0.65
        if ctx.bar.close >= sma20:
            confidence += 0.15
        if btc_ret > 0.0:
            confidence += 0.10
        confidence = min(1.0, confidence)

        self.last_traded_week = week_key
        self.entry_bar = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_monday_seasonality_drift",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "sma20": round(sma20, 2),
                "btc_return_pct": round(btc_ret, 2),
                "price": ctx.bar.close
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Add buffer cooldown on loss
        return_bps = position.get("return_bps", 0.0)
        if return_bps < 0:
            self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 16)
        else:
            self.cooldown_until_bar = max(self.cooldown_until_bar, ctx.bar_index + 8)