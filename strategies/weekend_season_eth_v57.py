from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 950.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_exit_bar = -999
        self.cooldown_bars = 12
        self.entry_price = 0.0
        self.bars_in_trade = 0
        self.trade_year_week = None

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_price = 0.0
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        highs = ctx.highs(50)
        lows = ctx.lows(50)
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        iso_year, iso_week, _ = ts.isocalendar()
        current_week_key = (iso_year, iso_week)

        # Risk and trend calculations
        atr = self._atr(highs, lows, closes, 14)
        ema50 = self._ema(closes, 50)
        if atr is None or ema50 is None:
            return None

        atr_pct = atr / closes[-1]
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50.0)

        # Active position management: structural timed exit & volatility trailing
        if ctx.has_position():
            self.bars_in_trade += 1
            if self.entry_price <= 0.0:
                self.entry_price = ctx.position.get("entry_price", closes[-1]) if ctx.position else closes[-1]

            return_bps = ((closes[-1] - self.entry_price) / self.entry_price) * 10000.0

            # Structural time exit: Sunday 20:00 - 23:00 UTC or Monday early morning
            is_weekend_close_window = (weekday == 6 and hour >= 20) or (weekday == 0 and hour <= 4)
            if is_weekend_close_window:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_timed_exit",
                        "bars_held": self.bars_in_trade,
                        "return_bps": round(return_bps, 1),
                        "weekday": weekday,
                        "hour": hour
                    }
                )

            # Max duration emergency exit (approx 56 hours = 56 bars)
            if self.bars_in_trade >= 56:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "max_duration_exhausted",
                        "bars_held": self.bars_in_trade,
                        "return_bps": round(return_bps, 1)
                    }
                )

            return None

        # Entry logic: strictly once per weekend calendar setup
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if self.trade_year_week == current_week_key:
            return None

        # Window: Friday 18:00 UTC to Saturday 04:00 UTC
        is_entry_window = (weekday == 4 and hour >= 18) or (weekday == 5 and hour <= 4)
        if not is_entry_window:
            return None

        # Filter out extreme crisis regimes and strong downtrends
        if crisis_score > 0.65 or fear_greed < 20:
            return None

        # Dynamic structure stops based on ATR
        dyn_sl_bps = max(350.0, min(700.0, atr_pct * 2.8 * 10000.0))
        dyn_tp_bps = max(600.0, min(1200.0, atr_pct * 4.5 * 10000.0))

        # Moderate confidence adjustment based on trend posture
        trend_aligned = closes[-1] >= ema50
        confidence = 0.75 if trend_aligned else 0.60

        self.trade_year_week = current_week_key
        self.entry_price = closes[-1]
        self.bars_in_trade = 0

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=dyn_sl_bps,
            take_profit_bps=dyn_tp_bps,
            horizon_seconds=172800,
            metadata={
                "reason": "weekend_drift_calendar_entry",
                "weekday": weekday,
                "hour": hour,
                "price": closes[-1],
                "ema50": round(ema50, 2),
                "atr_pct": round(atr_pct, 4),
                "fear_greed": fear_greed,
                "crisis_score": crisis_score
            }
        )