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
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup_period = 48
        self.last_exit_bar = -100
        self.cooldown_bars = 24  # at least 24 hours between trades

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_period + 5)
        if len(closes) < self.warmup_period:
            return None

        # Check market stress to avoid toxic weekend sell-offs
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        fear_greed = ctx.features.get("fear_greed_index", 50)

        ts = ctx.bar.timestamp
        weekday = ts.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = ts.hour

        # Handling open position: Sunday late evening scheduled exit
        if ctx.has_position():
            # Exit window: Sunday 20:00 UTC through Monday 02:00 UTC
            is_sunday_exit = (weekday == 6 and hour >= 20) or (weekday == 0 and hour <= 2)
            if is_sunday_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_cycle_completed",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close,
                    }
                )
            
            # Emergency regime exit if sudden crisis manifests mid-weekend
            if crisis_score > 0.75 or market_regime in ("CRISIS", "MELTDOWN"):
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "crisis_exit",
                        "crisis_score": crisis_score,
                        "regime": market_regime,
                    }
                )
            return None

        # Cooldown guard: ensure we don't re-enter in the same weekend
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Seasonal Entry Window: Friday 16:00 to 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # State/Regime filtering: skip entry if market is in severe breakdown or panic
        if crisis_score > 0.40 or market_regime in ("CRISIS", "MELTDOWN"):
            return None
        if fear_greed < 20:  # avoid extreme panic cascades
            return None

        # Technical structure: avoid buying straight into a waterfall
        rsi = self._rsi(closes, 14)
        ema_trend = self._ema(closes, 30)
        if rsi is None or ema_trend is None:
            return None

        # Do not buy if severely overbought (rsi > 68) or severe downtrend crash (close < 0.96 * ema30)
        if rsi > 68.0:
            return None
        if ctx.bar.close < (ema_trend * 0.96):
            return None

        # Dynamic confidence based on trend alignment and sentiment
        confidence = 0.65
        if ctx.bar.close >= ema_trend:
            confidence += 0.15
        if 40 <= fear_greed <= 75:
            confidence += 0.10
        confidence = min(1.0, max(0.5, confidence))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=400.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_seasonal_long",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "fear_greed": fear_greed,
                "ema30": round(ema_trend, 2),
                "close": ctx.bar.close,
            }
        )