from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -1
        self.in_weekend_trade = False

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)

        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        price = ctx.bar.close

        ema30 = self._ema(closes, 30)
        atr14 = self._atr(highs, lows, closes, 14)
        if ema30 is None or atr14 is None:
            return None

        atr_pct = (atr14 / price) * 10000.0
        fear_greed = ctx.features.get("fear_greed_index", 50)
        market_regime = ctx.market.get("regime", "NORMAL")

        # Exit conditions when holding a position
        if ctx.has_position():
            # Seasonality exit: Sunday night 20:00 - 23:00 UTC or Monday morning
            is_sunday_exit = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 4)
            # Market regime shock protection
            is_crisis = market_regime in ("CRISIS", "MELTDOWN")

            if is_sunday_exit or is_crisis:
                reason = "crisis_regime_exit" if is_crisis else "weekend_window_expired"
                self.last_exit_bar = ctx.bar_index
                self.in_weekend_trade = False
                return ctx.signal(
                    "flat",
                    confidence=0.8 if is_crisis else 0.6,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                        "market_regime": market_regime,
                        "bars_held": ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0
                    }
                )
            return None

        # Entry rules
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Primary window: Friday 16:00 to 23:00 UTC
        is_friday_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_window:
            return None

        # Relative state filters
        trend_support = price >= (ema30 * 0.985)
        safe_sentiment = fear_greed >= 20
        calm_volatility = atr_pct < 250.0

        if trend_support and safe_sentiment and calm_volatility:
            self.entry_bar = ctx.bar_index
            self.in_weekend_trade = True

            confidence = 0.65
            if price > ema30:
                confidence += 0.15
            if fear_greed > 45:
                confidence += 0.10
            confidence = min(confidence, 1.0)

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=500.0,
                take_profit_bps=650.0,
                horizon_seconds=172800,
                metadata={
                    "reason": "friday_evening_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "price": price,
                    "ema30": ema30,
                    "atr_bps": atr_pct,
                    "fear_greed": fear_greed
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.in_weekend_trade = False