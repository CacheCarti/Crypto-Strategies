from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolMondaySeasonality(Strategy):
    METADATA = {
        "name": "SolMondaySeasonality",
        "domain": "sol_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 64800,  # ~18 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_bar = -999
        self.cooldown_bars = 16
        self.entry_bar_idx = -999

    def _ema(self, values, period: int):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
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
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Sunday is 6, Monday is 0, Tuesday is 1
        hour = dt.hour
        current_price = ctx.bar.close

        # Check existing position for exit conditions
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            # Time-based exits: end of Monday drift or max hold reached
            exit_by_time = (weekday == 0 and hour >= 21) or (weekday == 1 and hour >= 0) or (bars_held >= 22)
            
            # Momentum breakdown exit: if RSI weakens drastically below 42
            rsi_val = self._rsi(closes, 14)
            rsi_breakdown = (rsi_val is not None and rsi_val < 38.0 and bars_held >= 4)

            if exit_by_time or rsi_breakdown:
                reason = "monday_session_end" if exit_by_time else "rsi_breakdown"
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": reason,
                        "bars_held": bars_held,
                        "price": current_price,
                        "rsi": rsi_val,
                        "weekday": weekday,
                        "hour": hour
                    }
                )
            return None

        # Entry logic: Not in position, check cooldown
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Crisis / Meltdown filter
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Seasonal Entry Window: Sunday 16:00 UTC through Monday 08:00 UTC
        is_sunday_evening = (weekday == 6 and hour >= 16)
        is_monday_morning = (weekday == 0 and hour <= 8)
        if not (is_sunday_evening or is_monday_morning):
            return None

        # Price-based confirmation to avoid catching falling knives
        rsi = self._rsi(closes, 14)
        ema_fast = self._ema(closes, 9)
        ema_slow = self._ema(closes, 21)

        if rsi is None or ema_fast is None or ema_slow is None:
            return None

        # Entry trigger: Price not severely overbought (RSI between 38 and 64)
        # and price is holding above or near short-term momentum (EMA9)
        momentum_ok = (38.0 <= rsi <= 64.0) and (current_price >= ema_fast * 0.995)
        trend_aligned = current_price >= ema_slow * 0.985

        if momentum_ok and trend_aligned:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar_idx = ctx.bar_index
            
            # Dynamic confidence based on RSI sweet spot and trend regime
            confidence = 0.70
            trend_regime = ctx.market.get("trend_regime", "neutral")
            if trend_regime == "bull":
                confidence = 0.85
            elif trend_regime == "bear":
                confidence = 0.55

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=320.0,
                take_profit_bps=520.0,
                horizon_seconds=72000,
                metadata={
                    "reason": "sunday_monday_seasonality_momentum",
                    "rsi": rsi,
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "price": current_price,
                    "weekday": weekday,
                    "hour": hour,
                    "trend_regime": trend_regime
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index