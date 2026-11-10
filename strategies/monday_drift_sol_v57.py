from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 420.0,
        "declared_tp_bps": 680.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.entry_price = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
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
        self.entry_bar = -999
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)

        if len(closes) < 35:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        price = ctx.bar.close

        atr = self._atr(highs, lows, closes, period=14)
        ema24 = self._ema(closes, period=24)
        if atr is None or ema24 is None or price <= 0:
            return None

        # Manage open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 1
            ret_bps = ((price - self.entry_price) / self.entry_price) * 10000.0 if self.entry_price > 0 else 0.0

            # Structural timed exit: Close by Monday 21:00 UTC or if held over into Tuesday
            time_exit_due = (weekday == 0 and hour >= 21) or (weekday == 1 and hour >= 2)
            
            # Volatility trailing stop: close if giving back ATR multiple or hitting structural stop
            vol_stop_hit = ret_bps < -360.0

            if time_exit_due or vol_stop_hit:
                reason = "monday_timed_session_close" if time_exit_due else "volatility_stop_triggered"
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "ret_bps": round(ret_bps, 2),
                        "weekday": weekday,
                        "hour": hour,
                        "price": price,
                    }
                )
            return None

        # Position entry gating
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Skip during severe market disruptions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.65 or regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Entry window: Sunday late UTC (18:00 to 23:00)
        is_sunday_window = (weekday == 6 and hour >= 18)
        # Fallback entry: Early Monday morning dip (00:00 to 04:00 UTC)
        is_monday_early = (weekday == 0 and hour <= 4)

        if not (is_sunday_window or is_monday_early):
            return None

        # Price structure condition: Ensure price is holding above recent local consolidation
        # and not in a free-fall (price > 98.5% of 24h EMA, avoiding severe downtrend traps)
        if price < ema24 * 0.985:
            return None

        # Dynamic stop-loss & take-profit derived from current volatility (ATR)
        atr_bps = (atr / price) * 10000.0
        stop_bps = max(260.0, min(500.0, atr_bps * 1.8))
        tp_bps = max(450.0, min(800.0, atr_bps * 3.2))

        # Confidence based on proximity to EMA support and trend context
        trend_regime = ctx.market.get("trend_regime", "neutral")
        confidence = 0.65
        if trend_regime == "bull":
            confidence = 0.85
        elif trend_regime == "bear":
            confidence = 0.55

        self.entry_bar = ctx.bar_index
        self.entry_price = price

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=stop_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=86400,
            metadata={
                "reason": "sunday_monday_seasonality_reversal",
                "weekday": weekday,
                "hour": hour,
                "price": price,
                "ema24": round(ema24, 2),
                "atr_bps": round(atr_bps, 1),
                "stop_bps": round(stop_bps, 1),
                "tp_bps": round(tp_bps, 1),
                "trend_regime": trend_regime,
            }
        )