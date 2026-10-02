from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolWeekdaySeasonality(Strategy):
    METADATA = {
        "name": "SolWeekdaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 720.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 55,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.last_traded_day_key = ""
        self.entry_setup = ""

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
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_setup = ""

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(55)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Tuesday=1, ..., Sunday=6
        hour = ts.hour
        day_key = f"{ts.year}_{ts.month}_{ts.day}"

        rsi = self._rsi(closes, period=14) or 50.0
        ema20 = self._ema(closes, period=20) or current_price
        ema50 = self._ema(closes, period=50) or current_price
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # ------------------ EXIT LOGIC ------------------
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long":
                # Exit Sunday trades late Monday / early Tuesday
                if self.entry_setup == "sunday_weekly_recovery" and ((weekday == 0 and hour >= 21) or weekday == 1):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "monday_session_complete_exit",
                            "weekday": weekday,
                            "hour": hour,
                            "rsi": round(rsi, 2),
                            "price": round(current_price, 2),
                        },
                    )

                # Exit Mid-week trades late Thursday
                if self.entry_setup == "midweek_rebound" and ((weekday == 3 and hour >= 21) or weekday == 4):
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.75,
                        metadata={
                            "reason": "thursday_session_complete_exit",
                            "weekday": weekday,
                            "hour": hour,
                            "rsi": round(rsi, 2),
                            "price": round(current_price, 2),
                        },
                    )

                # Technical Overbought Exit (Take profit before full time)
                if rsi > 76.0 and current_price > ema20:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.85,
                        metadata={
                            "reason": "rsi_overbought_profit_take",
                            "rsi": round(rsi, 2),
                            "price": round(current_price, 2),
                            "ema20": round(ema20, 2),
                        },
                    )

                # Meltdown emergency de-risk
                if crisis_score > 0.70:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.90,
                        metadata={
                            "reason": "crisis_score_emergency_exit",
                            "crisis_score": round(crisis_score, 2),
                            "price": round(current_price, 2),
                        },
                    )

            return None

        # ------------------ ENTRY LOGIC ------------------
        # Mandatory cooldown after exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Do not enter multiple times on the same calendar day
        if self.last_traded_day_key == day_key:
            return None

        # Filter out extreme market crises
        if crisis_score >= 0.60:
            return None

        # Avoid entering during excessive positive perpetual funding (crowded longs)
        if funding_rate > 0.00045:
            return None

        # Setup 1: Sunday Late Accumulation (Weekly Weekend Recovery)
        # Captures weekend liquidity vacuum into Monday morning institutional reopen
        if weekday == 6 and (18 <= hour <= 23):
            if rsi < 64.0 and current_price > (ema50 * 0.94):
                confidence = 0.75
                if rsi < 42.0:
                    confidence += 0.15
                if current_price > ema20:
                    confidence += 0.05
                confidence = min(0.95, confidence)

                self.last_traded_day_key = day_key
                self.entry_setup = "sunday_weekly_recovery"

                return ctx.signal(
                    "long",
                    confidence=round(confidence, 2),
                    stop_loss_bps=380.0,
                    take_profit_bps=720.0,
                    horizon_seconds=86400,
                    metadata={
                        "reason": "sunday_weekly_seasonality_long",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema20": round(ema20, 2),
                        "ema50": round(ema50, 2),
                        "funding_rate": round(funding_rate, 6),
                        "price": round(current_price, 2),
                    },
                )

        # Setup 2: Mid-Week Wednesday Dip Accumulation into Thursday Run
        if weekday == 2 and (18 <= hour <= 23):
            # Only enter if not already in deep breakdown or overbought
            if 30.0 <= rsi <= 60.0 and current_price > (ema50 * 0.96):
                confidence = 0.70
                if current_price >= ema20:
                    confidence += 0.10
                if rsi < 45.0:
                    confidence += 0.10
                confidence = min(0.90, confidence)

                self.last_traded_day_key = day_key
                self.entry_setup = "midweek_rebound"

                return ctx.signal(
                    "long",
                    confidence=round(confidence, 2),
                    stop_loss_bps=380.0,
                    take_profit_bps=720.0,
                    horizon_seconds=86400,
                    metadata={
                        "reason": "wednesday_midweek_dip_long",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema20": round(ema20, 2),
                        "ema50": round(ema50, 2),
                        "funding_rate": round(funding_rate, 6),
                        "price": round(current_price, 2),
                    },
                )

        return None