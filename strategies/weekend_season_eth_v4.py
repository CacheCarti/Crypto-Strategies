from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class WeekendSeasonalityAlpha(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityAlpha",
        "domain": "eth_usdc",
        "declared_sl_bps": 380.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 129600,  # ~36 hours typical swing hold
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_period = 12
        self.ema_slow_period = 26
        self.rsi_period = 14
        self.atr_period = 14
        self.cooldown_bars = 10
        self.last_trade_bar = -999
        self.last_exit_bar = -999
        self.win_streak = 0

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        return_bps = position.get("return_bps", 0.0)
        if return_bps > 0:
            self.win_streak = min(self.win_streak + 1, 3)
        else:
            self.win_streak = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Technical indicators
        ema_fast = self._ema(closes, self.ema_fast_period)
        ema_slow = self._ema(closes, self.ema_slow_period)
        rsi = self._rsi(closes, self.rsi_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        if ema_fast is None or ema_slow is None or rsi is None or atr is None:
            return None

        current_price = ctx.bar.close
        timestamp = ctx.bar.timestamp
        weekday = timestamp.weekday()  # Monday is 0, Sunday is 6
        hour = timestamp.hour

        # Market regime & sentiment filters
        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Position Management & Time-Based Exits
        if ctx.has_position():
            # Weekend Exit: Sunday 21:00 UTC through Monday 02:00 UTC
            is_weekend_exit = (weekday == 6 and hour >= 21) or (weekday == 0 and hour <= 2)
            # Mid-week Exit: Thursday 20:00 UTC
            is_midweek_exit = weekday == 3 and hour >= 20
            # Technical Overbought Exhaustion Exit
            is_rsi_overbought = rsi > 76.0

            if is_weekend_exit or is_midweek_exit or is_rsi_overbought:
                exit_reason = (
                    "weekend_session_close"
                    if is_weekend_exit
                    else ("midweek_session_close" if is_midweek_exit else "rsi_overbought_exit")
                )
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "weekday": weekday,
                        "hour": hour,
                    },
                )
            return None

        # Mandatory cooldown guard
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_trade = ctx.bar_index - self.last_trade_bar
        if bars_since_exit < self.cooldown_bars or bars_since_trade < self.cooldown_bars:
            return None

        # Severe crisis avoidance
        if market_regime in ("CRISIS", "MELTDOWN") or fear_greed < 15.0:
            return None

        # Calendar Entry Setup 1: Weekend Inflow (Friday 16:00 to Saturday 08:00 UTC)
        # Price must show constructive momentum: RSI not overbought, not breaking down hard
        is_weekend_window = (weekday == 4 and hour >= 16) or (weekday == 5 and hour <= 8)
        weekend_long_trigger = (
            is_weekend_window
            and 38.0 <= rsi <= 68.0
            and current_price >= ema_slow * 0.985
        )

        # Calendar Entry Setup 2: Mid-Week Turnaround (Tuesday 14:00 to Wednesday 06:00 UTC)
        # Dip buying setup with fast EMA confirmation
        is_midweek_window = (weekday == 1 and hour >= 14) or (weekday == 2 and hour <= 6)
        midweek_long_trigger = (
            is_midweek_window
            and 32.0 <= rsi <= 56.0
            and current_price > ema_fast
            and ema_fast > ema_slow * 0.99
        )

        if weekend_long_trigger or midweek_long_trigger:
            setup_name = "weekend_inflow_entry" if weekend_long_trigger else "midweek_dip_reversal"
            base_confidence = 0.65
            adaptive_boost = min(self.win_streak * 0.05, 0.15)
            confidence = min(base_confidence + adaptive_boost, 0.90)

            # Volatility-adjusted stop and profit target
            atr_pct = (atr / current_price) * 10000.0  # in bps
            stop_loss_bps = max(280.0, min(500.0, atr_pct * 1.8))
            take_profit_bps = max(450.0, min(800.0, atr_pct * 3.2))

            self.last_trade_bar = ctx.bar_index

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=round(stop_loss_bps, 1),
                take_profit_bps=round(take_profit_bps, 1),
                metadata={
                    "reason": setup_name,
                    "rsi": round(rsi, 2),
                    "ema_fast": round(ema_fast, 2),
                    "ema_slow": round(ema_slow, 2),
                    "atr_bps": round(atr_pct, 1),
                    "fear_greed": round(fear_greed, 1),
                    "weekday": weekday,
                    "hour": hour,
                    "win_streak": self.win_streak,
                },
            )

        return None