from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -100
        self.cooldown_bars = 12
        self.last_traded_week = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1 - k)
        return val

    def _atr(self, highs, lows, closes, period: int = 14):
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
        closes = ctx.closes(50)
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        price = ctx.bar.close

        ema30 = self._ema(closes, 30)
        if ema30 is None:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fg_index = ctx.features.get("fear_greed_index", 50)

        # Handle active position exit: late Sunday UTC (>= 20:00) or Monday morning
        if ctx.has_position():
            if (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 4):
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "weekend_drift_expiry_sunday_night",
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                    }
                )
            if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.6:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "risk_regime_abort",
                        "regime": regime,
                        "crisis_score": crisis_score,
                        "price": price,
                    }
                )
            return None

        # Cooldown guard after prior trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Prevent duplicate entries in the same calendar week
        cal_week = ts.isocalendar()[1]
        if cal_week == self.last_traded_week:
            return None

        # Entry window: Friday between 16:00 and 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Safety filters: skip if market is collapsing or in extreme fear
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45 or fg_index < 20:
            return None

        # Trend and volatility check: price should not be crashing deeply below short-term baseline
        atr = self._atr(highs, lows, closes, 14)
        atr_val = atr if atr is not None else (price * 0.015)
        
        # Don't buy if sharp immediate downtrend (price more than 1.5 ATR below 30 EMA)
        if price < (ema30 - 1.5 * atr_val):
            return None

        # Confidence scaled by trend alignment and sentiment
        base_conf = 0.65
        if price > ema30:
            base_conf += 0.15
        if fg_index >= 45:
            base_conf += 0.10
        confidence = min(0.95, base_conf)

        self.last_traded_week = cal_week

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=550.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "weekend_seasonality_friday_entry",
                "weekday": weekday,
                "hour": hour,
                "price": price,
                "ema30": round(ema30, 2),
                "fear_greed": fg_index,
                "regime": regime,
            }
        )