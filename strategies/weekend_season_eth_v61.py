from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 129600,  # ~36 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 18
        self.entry_bar_idx = 0
        self.traded_this_window = False
        self.last_seen_week = -1

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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()

        if iso_week != self.last_seen_week:
            self.last_seen_week = iso_week
            self.traded_this_window = False

        rsi = self._rsi(closes, 14)
        ema_trend = self._ema(closes, 24)
        if rsi is None or ema_trend is None:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            
            # Sunday evening scheduled rotation/exit (20:00 - 23:00 UTC)
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "sunday_evening_session_close",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "close": round(ctx.bar.close, 2)
                    }
                )

            # Technical profit target / overbought dynamic exit prior to Sunday
            if rsi > 76.0 and bars_held >= 12:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "weekend_dynamic_tp_rsi_overbought",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "close": round(ctx.bar.close, 2)
                    }
                )

            # Fallback timeout to protect capital if held past early Monday
            if weekday == 0 and hour >= 4:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "monday_morning_fallback_exit",
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                        "close": round(ctx.bar.close, 2)
                    }
                )
            return None

        # Cooldown & session gate
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if self.traded_this_window:
            return None

        # Regime safety filter: avoid severe market crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Entry window: Friday 16:00 UTC to Saturday 04:00 UTC
        is_friday_entry = (weekday == 4 and hour >= 16)
        is_saturday_entry = (weekday == 5 and hour <= 4)
        in_entry_window = is_friday_entry or is_saturday_entry

        if in_entry_window:
            # Momentum / value filter: not severely overbought, above short-term trend or bouncing
            price = ctx.bar.close
            price_filter = price >= (ema_trend * 0.995)
            momentum_filter = 36.0 <= rsi <= 68.0

            if price_filter and momentum_filter:
                self.entry_bar_idx = ctx.bar_index
                self.traded_this_window = True
                
                # Confidence scaling based on trend alignment and RSI positioning
                confidence = 0.65
                if price > ema_trend:
                    confidence += 0.10
                if 42.0 <= rsi <= 58.0:
                    confidence += 0.10

                return ctx.signal(
                    "long",
                    confidence=min(confidence, 0.9),
                    stop_loss_bps=350.0,
                    take_profit_bps=550.0,
                    horizon_seconds=129600,
                    metadata={
                        "reason": "friday_weekend_drift_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema_trend": round(ema_trend, 2),
                        "crisis_score": round(crisis_score, 2),
                        "close": round(price, 2)
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index