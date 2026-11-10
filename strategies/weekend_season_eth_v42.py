from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "Weekend Seasonality Regime ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 850.0,
        "declared_hold_seconds": 172800,  # ~48 hour target weekend hold
        "warmup_bars": 60,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_fast_len = 24
        self.ema_slow_len = 72
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.last_traded_week = -1

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        weekday = current_time.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = current_time.hour
        iso_year, iso_week, _ = current_time.isocalendar()

        has_pos = ctx.has_position()

        # Exit Condition: Sunday evening (20:00 UTC or later) or early Monday
        if has_pos:
            is_exit_window = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 4)
            if is_exit_window:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_hold_target_reached",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    },
                )
            return None

        # Entry gating: mandatory cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Gate on trading once per weekend
        if self.last_traded_week == iso_week:
            return None

        # Entry Window: Friday 18:00 UTC to Saturday 04:00 UTC
        in_entry_window = (weekday == 4 and hour >= 18) or (weekday == 5 and hour <= 4)
        if not in_entry_window:
            return None

        # Regime & Macro Filters:
        # Prior evidence shows unconditioned weekend longs fail in severe crisis or heavy bear regimes.
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        trend_regime = ctx.market.get("trend_regime", "neutral")
        if trend_regime == "bear":
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed < 20.0:
            return None

        # Technical Momentum / Trend Alignment Filter
        ema_fast = self._ema(closes, self.ema_fast_len)
        ema_slow = self._ema(closes, self.ema_slow_len)
        if ema_fast is None or ema_slow is None:
            return None

        # Ensure market is not collapsing into the weekend: fast EMA must not trail slow EMA heavily
        if ema_fast < ema_slow * 0.985:
            return None

        atr = self._atr(highs, lows, closes, 14)
        atr_pct = (atr / ctx.bar.close) * 10000.0 if atr else 150.0

        # Dynamic stop and target scaled mildly to volatility
        sl_bps = max(400.0, min(750.0, atr_pct * 2.5))
        tp_bps = max(600.0, min(1200.0, sl_bps * 1.6))

        confidence = 0.65
        if ema_fast > ema_slow:
            confidence += 0.15
        if fear_greed >= 45:
            confidence += 0.10

        self.last_traded_week = iso_week
        return ctx.signal(
            "long",
            confidence=min(0.95, confidence),
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=self.METADATA["declared_hold_seconds"],
            metadata={
                "reason": "weekend_seasonality_favorable_regime",
                "weekday": weekday,
                "hour": hour,
                "ema_fast": round(ema_fast, 2),
                "ema_slow": round(ema_slow, 2),
                "fear_greed": fear_greed,
                "regime": market_regime,
                "trend": trend_regime,
            },
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index