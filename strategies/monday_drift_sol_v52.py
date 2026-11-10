from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 64800,  # ~18 hours hold target
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period_trend = 48
        self.period_rsi = 14
        self.last_trade_bar = -999
        self.cooldown_bars = 36  # Wait at least 36 hours after entering before another entry
        self.entry_price = 0.0

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
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period_trend + 10)
        if len(closes) < self.period_trend + 5:
            return None

        current_price = ctx.bar.close
        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday is 0, Sunday is 6
        hour = ts.hour

        # Check existing position management
        if ctx.has_position():
            # Exit seasonality window: Monday 21:00 UTC or Tuesday onwards
            is_exit_time = (weekday == 0 and hour >= 21) or (weekday == 1)
            
            # Or market regime emergency exit
            regime = ctx.market.get("regime", "NORMAL")
            crisis_score = ctx.market.get("crisis_score", 0.0)

            if is_exit_time or regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.65:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "seasonal_window_end" if is_exit_time else "regime_degraded",
                        "weekday": weekday,
                        "hour": hour,
                        "regime": regime,
                        "crisis_score": crisis_score,
                    }
                )
            return None

        # Entry logic: Cooldown check
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Seasonality Entry Window: Sunday 18:00 to 23:00 UTC
        in_entry_window = (weekday == 6 and hour >= 18)
        if not in_entry_window:
            return None

        # Regime Gate from prior evidence: Monday drift fails in severe bear trend or crisis
        trend_regime = ctx.market.get("trend_regime", "neutral")
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ["CRISIS", "MELTDOWN"] or trend_regime == "bear":
            return None

        # Sentiment Gate: Extreme fear suppresses recovery bounce
        fg = ctx.features.get("fear_greed_index", 50.0)
        if fg < 25.0:
            return None

        # Price & Momentum filter: price must be stabilizing around or above medium EMA, not crashing free-fall
        ema_trend = self._ema(closes, self.period_trend)
        rsi = self._rsi(closes, self.period_rsi)

        if ema_trend is None or rsi is None:
            return None

        # Confirm not heavily overbought, and not breaking through local support strongly
        # Edge is best when RSI is between 38 and 62 (healthy pullback or neutral consolidation)
        if not (38.0 <= rsi <= 64.0):
            return None

        if current_price < ema_trend * 0.965:
            # Too steep a weekend plunge (likely persistent downtrend)
            return None

        self.last_trade_bar = ctx.bar_index
        self.entry_price = current_price

        # Scale confidence based on trend support
        conf = 0.65
        if current_price > ema_trend:
            conf += 0.15

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=self.METADATA["declared_sl_bps"],
            take_profit_bps=self.METADATA["declared_tp_bps"],
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "sunday_late_drift_setup",
                "weekday": weekday,
                "hour": hour,
                "rsi": rsi,
                "ema_trend": ema_trend,
                "price": current_price,
                "trend_regime": trend_regime,
                "fear_greed": fg,
            }
        )