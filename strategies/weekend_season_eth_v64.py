from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 500.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        val = sum(values[:period]) / period
        for v in values[period:]:
            val = v * k + val * (1.0 - k)
        return val

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
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
        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        price = ctx.bar.close

        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Target exit: Sunday evening between 20:00 and 23:00 UTC, or safety hold limit (~54 1h bars)
            is_sunday_exit = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 2)
            if is_sunday_exit or bars_held >= 54:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_target_reached",
                        "weekday": weekday,
                        "hour": hour,
                        "bars_held": bars_held,
                        "price": price
                    }
                )
            return None

        # Check entry cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calendar window: Friday 16:00 UTC to 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Regime / Crisis filter: avoid entering during extreme market panics
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Indicators for confirmation: Trend EMA, Volatility sanity (ATR)
        ema24 = self._ema(closes, 24)
        atr14 = self._atr(highs, lows, closes, 14)
        if ema24 is None or atr14 is None:
            return None

        # Avoid extreme high-volatility blow-off entering weekend
        atr_pct = (atr14 / price) * 100.0
        if atr_pct > 4.0:
            return None

        # Cross-asset filter: BTC 24h momentum must not be heavily dumping
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret < -4.5:
            return None

        # Price confirmation: ETH must be holding reasonably above or near short-term trend
        # Allowing moderate dips (>= 98.8% of EMA24) to capture drift without catching knife
        if price < ema24 * 0.988:
            return None

        # Confidence calculation
        confidence = 0.65
        if price >= ema24:
            confidence += 0.10
        if btc_ret > 0.0:
            confidence += 0.10
        confidence = min(confidence, 0.90)

        self.entry_bar = ctx.bar_index
        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=500.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_weekend_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "price": price,
                "ema24": round(ema24, 2),
                "atr_pct": round(atr_pct, 2),
                "btc_ret": round(btc_ret, 2)
            }
        )