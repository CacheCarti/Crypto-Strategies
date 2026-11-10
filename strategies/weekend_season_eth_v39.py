from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entered_this_weekend = False
        self.last_weekend_id = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(60)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        is_calm_or_normal = ctx.regime in ["calm", "normal"]

        # Track distinct weekends to ensure at most one entry per weekend
        cal = ts.isocalendar()
        current_weekend_id = cal[0] * 100 + cal[1]
        if current_weekend_id != self.last_weekend_id:
            self.last_weekend_id = current_weekend_id
            self.entered_this_weekend = False

        # Manage open position
        if ctx.has_position():
            # Scheduled exit: late Sunday (UTC 20:00 - 23:00) or early Monday
            if (weekday == 6 and hour >= 20) or weekday == 0:
                rsi_val = self._rsi(closes, 14) or 50.0
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_season_exit_target_reached",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2),
                    }
                )

            # Volatility or crisis emergency exit
            if ctx.regime in ["crisis", "volatile"]:
                trend_regime = ctx.market.get("trend_regime", "neutral")
                if trend_regime == "bear":
                    return ctx.signal(
                        "flat",
                        confidence=0.9,
                        metadata={
                            "reason": "weekend_season_crisis_exit",
                            "regime": ctx.regime,
                            "trend_regime": trend_regime,
                        }
                    )
            return None

        # Check cooldown after previous trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Allow entry on Friday evening (18:00 - 23:00) or early Saturday (00:00 - 06:00)
        in_entry_window = (weekday == 4 and hour >= 18) or (weekday == 5 and hour <= 6)
        if not in_entry_window or self.entered_this_weekend:
            return None

        # Price-based confirmation filters
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        rsi = self._rsi(closes, 14)

        if ema20 is None or ema50 is None or rsi is None:
            return None

        price = ctx.bar.close

        # Avoid extreme overbought entries or severe downtrend knife-catches
        if rsi > 68.0 or rsi < 32.0:
            return None

        # Trend and regime qualification
        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear" and price < ema50:
            return None

        # Momentum filter: prefer price not dropping precipitously below short-term EMA
        if price < ema20 * 0.975:
            return None

        # Calculate confidence based on market state
        confidence = 0.65
        if price > ema20:
            confidence += 0.1
        if trend_regime == "bull":
            confidence += 0.1
        if is_calm_or_normal:
            confidence += 0.05
        confidence = min(0.95, confidence)

        self.entered_this_weekend = True

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=550.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "weekend_season_long_entry",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "price": round(price, 2),
                "ema20": round(ema20, 2),
                "ema50": round(ema50, 2),
                "regime": ctx.regime,
                "trend_regime": trend_regime,
            }
        )