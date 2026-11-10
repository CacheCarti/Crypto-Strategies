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
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 35
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.traded_this_weekend = False
        self.current_weekend_id = -1

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        closes = ctx.closes(self.warmup)
        if len(closes) < self.warmup:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = dt.hour

        # Track distinct calendar weeks to prevent re-entering within the same weekend
        year_week = dt.isocalendar()
        weekend_id = year_week[0] * 100 + year_week[1]
        if weekend_id != self.current_weekend_id:
            self.current_weekend_id = weekend_id
            self.traded_this_weekend = False

        # EXIT LOGIC: Sunday late evening (20:00 - 23:00 UTC) or early Monday
        if ctx.has_position():
            is_exit_window = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 2)
            if is_exit_window:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_expiry",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Cooldown guard after any stop/exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Prevent multiple trades during the same weekend window
        if self.traded_this_weekend:
            return None

        # ENTRY WINDOW: Friday 18:00 to 23:00 UTC
        in_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not in_entry_window:
            return None

        # Market regime filter from engine
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Technical & Cross-Asset Confirmation (Addressing dominant oos_reject failure)
        # 1. Price above EMA24 on hourly (session trend stabilization)
        ema24 = self._ema(closes, 24)
        if ema24 is None or ctx.bar.close < ema24 * 0.995:
            return None

        # 2. Volatility state: ensure not in hyper-volatility shock
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr14 = self._atr(highs, lows, closes, 14)
        if atr14 is not None and (atr14 / ctx.bar.close) > 0.045:
            return None  # Skip if ETH hourly ATR is > 4.5% of price

        # 3. Cross-asset momentum confirmation: BTC 24h return not severely bleeding
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        if btc_ret < -4.5:
            return None

        # Determine signal confidence based on alignment
        confidence = 0.65
        if ctx.bar.close > ema24:
            confidence += 0.10
        if btc_ret > 0.0:
            confidence += 0.10

        self.traded_this_weekend = True

        return ctx.signal(
            "long",
            confidence=min(0.90, confidence),
            stop_loss_bps=400.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_evening_seasonal_entry",
                "weekday": weekday,
                "hour": hour,
                "btc_return_pct": btc_ret,
                "ema24": round(ema24, 2) if ema24 else None,
                "regime": regime,
                "atr14_bps": round((atr14 / ctx.bar.close) * 10000.0, 1) if atr14 else None,
            },
        )