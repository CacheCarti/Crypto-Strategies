from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold across weekend
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.last_traded_week = -1
        self.period_ema = 50
        self.period_rsi = 14

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
        closes = ctx.closes(self.period_ema + 15)
        if len(closes) < self.period_ema + 1:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        current_price = ctx.bar.close
        ema50 = self._ema(closes, self.period_ema)
        rsi14 = self._rsi(closes, self.period_rsi)

        if ema50 is None or rsi14 is None:
            return None

        # Check existing position for Sunday evening planned exit
        if ctx.has_position():
            # Exit late Sunday (20:00 UTC - 23:00 UTC) to capture weekend drift
            if weekday == 6 and hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_target_end_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "price": current_price,
                        "rsi": round(rsi14, 2)
                    }
                )
            # Monday failsafe exit if still open
            if weekday == 0 and hour >= 2:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "monday_morning_cutoff",
                        "weekday": weekday,
                        "hour": hour,
                        "price": current_price
                    }
                )
            return None

        # Cooldown guard after recent exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Prevent duplicate entries in the same weekend
        if self.last_traded_week == iso_week:
            return None

        # Entry window: Friday evening (18:00 to 23:00 UTC)
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Regime and market filters to avoid dominant oos_reject traps
        # Avoid entering during systemic crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Price-based momentum/trend filter:
        # Require price above 0.985 * EMA50 (not heavily breaking down)
        # and RSI between 35 and 68 (not in a freefall or exhausted blow-off)
        trend_ok = current_price >= (ema50 * 0.985)
        rsi_ok = 35.0 <= rsi14 <= 68.0

        if not (trend_ok and rsi_ok):
            return None

        # Base confidence scaled by trend alignment
        confidence = 0.70
        if current_price > ema50:
            confidence = 0.85

        self.last_traded_week = iso_week

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=450.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_weekend_seasonality_entry",
                "price": current_price,
                "ema50": round(ema50, 2),
                "rsi": round(rsi14, 2),
                "weekday": weekday,
                "hour": hour,
                "regime": market_regime
            }
        )