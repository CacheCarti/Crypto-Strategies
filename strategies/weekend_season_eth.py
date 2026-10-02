from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class WeekendDriftSeasonality(Strategy):
    METADATA = {
        "name": "Weekend Drift Seasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 172800,  # ~48 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_entry_bar = -100
        self.cooldown_bars = 6
        self.last_traded_week_id = None

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

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

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(60)
        highs = ctx.highs(60)
        lows = ctx.lows(60)

        if len(closes) < 50:
            return None

        price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday
        hour = ts.hour
        week_id = (ts.year, ts.isocalendar()[1])

        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        rsi = self._rsi(closes, 14)
        atr = self._atr(highs, lows, closes, 14)

        if ema20 is None or ema50 is None or rsi is None or atr is None:
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)

        # -------------------------------------------------------------
        # 1. POSITION MANAGEMENT & EXITS
        # -------------------------------------------------------------
        if ctx.has_position():
            # Weekend expiry: late Sunday (>= 20:00 UTC) or early Monday
            if (weekday == 6 and hour >= 20) or (weekday == 0 and hour <= 2):
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_session_end",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "price": price,
                    },
                )

            # Mid-week RSI overbought profit-taking
            if weekday not in (5, 6) and rsi > 72.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "midweek_rsi_overbought_exit",
                        "rsi": round(rsi, 2),
                        "price": price,
                    },
                )

            # Emergency risk-off exit if market enters crisis mode
            if crisis_score > 0.75:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "crisis_regime_exit",
                        "crisis_score": round(crisis_score, 2),
                        "price": price,
                    },
                )

            return None

        # -------------------------------------------------------------
        # 2. ENTRY GATES & COOLDOWN
        # -------------------------------------------------------------
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        if crisis_score > 0.65:
            return None

        # -------------------------------------------------------------
        # 3. ENTRY SIGNALS
        # -------------------------------------------------------------

        # A) Weekend Long Seasonality Entry (Friday 18:00 - 23:00 UTC or early Saturday)
        is_friday_eve = weekday == 4 and hour >= 18
        is_saturday_morning = weekday == 5 and hour <= 3
        is_weekend_window = is_friday_eve or is_saturday_morning

        if is_weekend_window and self.last_traded_week_id != week_id:
            # Price filter: avoid entering during parabolic overbought tops or deep breakdown
            if 30.0 <= rsi <= 68.0 and price >= (ema50 * 0.94):
                confidence = 0.65
                if price > ema20:
                    confidence += 0.15
                if funding <= 0.0001:  # Favorable or neutral funding
                    confidence += 0.10

                self.last_entry_bar = ctx.bar_index
                self.last_traded_week_id = week_id

                return ctx.signal(
                    "long",
                    confidence=min(confidence, 1.0),
                    stop_loss_bps=380.0,
                    take_profit_bps=520.0,
                    horizon_seconds=172800,
                    metadata={
                        "reason": "friday_weekend_drift_long",
                        "weekday": weekday,
                        "hour": hour,
                        "rsi": round(rsi, 2),
                        "ema20": round(ema20, 2),
                        "ema50": round(ema50, 2),
                        "funding": funding,
                    },
                )

        # B) Mid-Week Mean Reversion Dip Entry (Tuesday/Wednesday oversold pullback)
        is_midweek_dip = weekday in (1, 2) and 10 <= hour <= 20
        if is_midweek_dip and rsi < 34.0 and price > (ema50 * 0.92):
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.70,
                stop_loss_bps=300.0,
                take_profit_bps=420.0,
                horizon_seconds=86400,
                metadata={
                    "reason": "midweek_oversold_dip_buy",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi": round(rsi, 2),
                    "price": price,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_entry_bar = ctx.bar_index