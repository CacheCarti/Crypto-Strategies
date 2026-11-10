from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonalityDrift(Strategy):
    METADATA = {
        "name": "SolMondaySeasonalityDrift",
        "domain": "sol_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 600.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 50,
        "required_features": ["funding_rate_solusdt", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 18
        self.last_exit_bar = -100
        self.last_entry_bar = -100
        self.entered_this_week = -1
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour
        bar_idx = ctx.bar_index

        rsi14 = self._rsi(closes, 14)
        ema20 = self._ema(closes, 20)
        if rsi14 is None or ema20 is None:
            return None

        # Manage open position
        if ctx.has_position():
            # Mandatory exit boundaries:
            # 1. Monday late US session close (Monday 21:00 UTC or later)
            # 2. Any time Tuesday onwards
            # 3. Dynamic RSI overbought exhaustion (> 75)
            # 4. Crisis protection
            is_monday_late = (weekday == 0 and hour >= 21)
            is_post_monday = (weekday >= 1 and weekday <= 4)
            rsi_exhausted = (rsi14 >= 76.0 and weekday == 0 and hour >= 16)
            is_crisis = ctx.market.get("regime", "NORMAL") in ["CRISIS", "MELTDOWN"]

            if is_monday_late or is_post_monday or rsi_exhausted or is_crisis:
                reason = "monday_window_end" if (is_monday_late or is_post_monday) else ("rsi_exhaustion" if rsi_exhausted else "crisis_risk_off")
                self.last_exit_bar = bar_idx
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "rsi14": round(rsi14, 2),
                        "close": ctx.bar.close
                    }
                )
            return None

        # Entry logic: strictly once per weekly cycle with post-exit cooldown
        cal_week = ts.isocalendar()[1]
        if cal_week == self.entered_this_week:
            return None
        if (bar_idx - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Event window: Sunday 18:00 UTC to Monday 08:00 UTC
        is_sunday_evening = (weekday == 6 and hour >= 18)
        is_monday_early = (weekday == 0 and hour <= 8)
        in_event_window = is_sunday_evening or is_monday_early

        if not in_event_window:
            return None

        # Defensive regime filters:
        # Avoid entering during acute market crises or extreme overheated funding
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ["CRISIS", "MELTDOWN"]:
            return None

        funding_sol = ctx.features.get("funding_rate_solusdt", 0.0)
        if funding_sol > 0.0007:  # >0.07% 8h funding indicates crowded long leverage
            return None

        # Price-based setup: SOL not deeply overbought, demonstrating base formation
        # RSI between 30 and 64; avoid chasing parabolic moves
        if 30.0 <= rsi14 <= 64.0 and ctx.bar.close >= (ema20 * 0.985):
            self.entered_this_week = cal_week
            self.last_entry_bar = bar_idx
            self.entry_price = ctx.bar.close

            confidence = 0.70
            if rsi14 < 48.0:
                confidence += 0.10
            if funding_sol <= 0.0001:
                confidence += 0.10

            return ctx.signal(
                "long",
                confidence=min(0.95, confidence),
                stop_loss_bps=400.0,
                take_profit_bps=600.0,
                horizon_seconds=86400,
                metadata={
                    "reason": "sunday_monday_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "rsi14": round(rsi14, 2),
                    "close": ctx.bar.close,
                    "funding_sol": round(funding_sol, 6)
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index