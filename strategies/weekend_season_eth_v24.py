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
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entered_this_weekend = False
        self.current_weekend_id = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        if len(closes) < 50:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour
        iso_year, iso_week, _ = dt.isocalendar()
        weekend_id = iso_year * 100 + iso_week

        if weekend_id != self.current_weekend_id:
            self.current_weekend_id = weekend_id
            self.entered_this_weekend = False

        if ctx.has_position():
            # Exit late Sunday (20:00 to 23:00 UTC) or early Monday
            if (weekday == 6 and hour >= 20) or weekday == 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_session_end_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry window: Friday 18:00 UTC through Saturday 04:00 UTC
        is_entry_window = (weekday == 4 and hour >= 18) or (weekday == 5 and hour <= 4)
        if not is_entry_window or self.entered_this_weekend:
            return None

        # Avoid extreme stress / crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            return None

        # Volatility state confirmation: ATR not blowing out into meltdown
        highs = ctx.highs(50)
        lows = ctx.lows(50)
        atr = self._atr(highs, lows, closes, 14)
        if atr is None:
            return None

        atr_pct = atr / ctx.bar.close
        if atr_pct > 0.05:
            return None

        # Trend and Cross-asset confirmation
        ema30 = self._ema(closes, 30)
        if ema30 is None:
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        # Avoid longing ETH if BTC is in free-fall over 24h
        if btc_ret < -4.0:
            return None

        # Mild trend alignment: ETH trading near or above medium-term EMA
        if ctx.bar.close < ema30 * 0.985:
            return None

        confidence = 0.70
        if ctx.bar.close > ema30:
            confidence += 0.10
        if btc_ret > 0.0:
            confidence += 0.10

        self.entered_this_weekend = True
        return ctx.signal(
            "long",
            confidence=min(confidence, 0.95),
            stop_loss_bps=500.0,
            take_profit_bps=650.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_weekend_drift_entry",
                "weekday": weekday,
                "hour": hour,
                "btc_ret_24h": round(btc_ret, 3),
                "ema30": round(ema30, 2),
                "atr_pct": round(atr_pct, 4),
                "crisis_score": round(crisis_score, 2),
                "close": ctx.bar.close
            }
        )