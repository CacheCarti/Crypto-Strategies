from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold across weekend
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -100
        self.weekend_entered_week = -1

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _atr(self, highs, lows, closes, period=14):
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
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour
        iso_calendar = ts.isocalendar()
        current_week = (iso_calendar[0], iso_calendar[1])

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Exit logic: Sunday evening (>= 20:00 UTC)
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if weekday == 6 and hour >= 20:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "sunday_evening_session_exit",
                            "weekday": weekday,
                            "hour": hour,
                            "close": ctx.bar.close,
                        }
                    )
            return None

        # Entry logic: Friday late afternoon/evening (UTC 16 to 23)
        # Avoid entering more than once per weekend
        if weekday == 4 and 16 <= hour <= 23:
            if self.weekend_entered_week == current_week:
                return None

            # Trend filter: ETH above EMA 24 to avoid counter-trend knife-catching
            ema24 = self._ema(closes, 24)
            if ema24 is None or ctx.bar.close <= ema24:
                return None

            # Cross-asset momentum confirmation: BTC 24h return must not be severely negative
            btc_ret = ctx.features.get("btc_return_pct", 0.0)
            if btc_ret < -2.0:
                return None

            # Volatility state: ensure not in extreme crisis/meltdown
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # ATR for dynamic stop sizing
            highs = ctx.highs(30)
            lows = ctx.lows(30)
            atr = self._atr(highs, lows, closes, 14)
            atr_bps = (atr / ctx.bar.close * 10000.0) if (atr and ctx.bar.close > 0) else 350.0
            
            sl_bps = max(300.0, min(550.0, atr_bps * 1.8))
            tp_bps = max(600.0, sl_bps * 2.0)

            confidence = 0.70
            if btc_ret > 0.0:
                confidence += 0.15

            self.weekend_entered_week = current_week
            return ctx.signal(
                "long",
                confidence=min(0.95, confidence),
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=172800,
                metadata={
                    "reason": "friday_weekend_drift_long",
                    "weekday": weekday,
                    "hour": hour,
                    "close": ctx.bar.close,
                    "ema24": ema24,
                    "btc_return_pct": btc_ret,
                    "atr_bps": atr_bps,
                    "regime": regime,
                }
            )

        return None