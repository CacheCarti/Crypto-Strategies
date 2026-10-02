from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendDriftSeasonality(Strategy):
    METADATA = {
        "name": "Weekend Drift Seasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 172800,  # ~48h hold window
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.rsi_period = 14
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.atr_period = 14
        self.last_exit_bar = -100
        self.cooldown_bars = 16
        self.last_traded_week = -1

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1.0)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup + 10)
        if len(closes) < self.warmup:
            return None

        highs = ctx.highs(self.warmup + 10)
        lows = ctx.lows(self.warmup + 10)
        
        rsi_val = self._rsi(closes, self.rsi_period)
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        atr_val = self._atr(highs, lows, closes, self.atr_period)

        if rsi_val is None or ema_fast is None or ema_slow is None or atr_val is None:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
        hour = dt.hour
        # ISO calendar week number to ensure at most one primary weekend trade cycle per week
        cal_week = dt.isocalendar()[1]

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        has_pos = ctx.has_position()

        # ----------------- EXIT LOGIC -----------------
        if has_pos:
            # 1. Scheduled Sunday evening exit (after 20:00 UTC)
            if weekday == 6 and hour >= 20:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "sunday_evening_calendar_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                        "rsi": round(rsi_val, 2),
                    }
                )

            # 2. Overextended momentum take-profit trigger (RSI > 75)
            if rsi_val > 75.0:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "rsi_overbought_weekend_exit",
                        "rsi": round(rsi_val, 2),
                        "close": ctx.bar.close,
                    }
                )

            # 3. Crisis regime protection
            if market_regime in ("CRISIS", "MELTDOWN"):
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.95,
                    metadata={
                        "reason": "crisis_regime_protection_exit",
                        "regime": market_regime,
                        "close": ctx.bar.close,
                    }
                )

            return None

        # ----------------- ENTRY LOGIC -----------------
        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Disallow trading during extreme market distress
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Primary entry window: Friday 18:00 UTC through Saturday 14:00 UTC
        is_friday_entry = (weekday == 4 and hour >= 18)
        is_saturday_entry = (weekday == 5 and hour <= 14)
        
        # Secondary midweek dip window (Tuesday-Wednesday night dip with oversold confirmation)
        is_midweek_dip = (weekday in (1, 2) and hour >= 20 and rsi_val < 38.0 and ctx.bar.close > ema_slow * 0.985)

        if (is_friday_entry or is_saturday_entry) and self.last_traded_week != cal_week:
            # Technical price-based filters for weekend drift
            # Avoid entering if heavily overbought or in a steep freefall
            if 34.0 <= rsi_val <= 68.0 and ctx.bar.close >= ema_slow * 0.975:
                # Dynamic confidence scaling
                confidence = 0.65
                if ema_fast > ema_slow:
                    confidence += 0.15
                if fear_greed >= 30:
                    confidence += 0.10
                confidence = min(0.95, max(0.50, confidence))

                # Dynamic ATR-informed stops
                atr_pct = (atr_val / ctx.bar.close) * 10000.0
                sl_bps = max(280.0, min(500.0, atr_pct * 2.2))
                tp_bps = max(420.0, min(750.0, atr_pct * 3.3))

                self.last_traded_week = cal_week
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=round(sl_bps, 1),
                    take_profit_bps=round(tp_bps, 1),
                    metadata={
                        "reason": "weekend_drift_entry",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi_val, 2),
                        "ema_fast": round(ema_fast, 2),
                        "ema_slow": round(ema_slow, 2),
                        "fear_greed": fear_greed,
                        "atr_bps": round(atr_pct, 1),
                    }
                )

        elif is_midweek_dip:
            # High conviction dip entry
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=320.0,
                take_profit_bps=480.0,
                metadata={
                    "reason": "midweek_reversion_dip_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi_val, 2),
                    "close": ctx.bar.close,
                    "ema_slow": round(ema_slow, 2),
                }
            )

        return None