from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityEth(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityEth",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.entry_price = 0.0
        self.entry_bar = 0

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_price = 0.0
        self.entry_bar = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = dt.hour
        current_price = ctx.bar.close

        # Manage existing open position
        if ctx.has_position():
            # Check exit conditions:
            # Target exit boundary: Sunday late evening (20:00 to 23:00 UTC)
            is_sunday_exit = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 2)
            
            # Catastrophic / Crisis regime early exit
            regime = ctx.market.get("regime", "NORMAL")
            crisis_score = ctx.market.get("crisis_score", 0.0)
            is_crisis = regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65

            if is_sunday_exit or is_crisis:
                reason = "weekend_window_expired" if is_sunday_exit else "market_crisis_exit"
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": reason,
                        "weekday": weekday,
                        "hour": hour,
                        "crisis_score": crisis_score,
                        "regime": regime,
                        "bars_held": ctx.bar_index - self.entry_bar
                    }
                )
            return None

        # Check entry cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisp calendar event boundary: Friday 18:00 to 23:00 UTC
        is_friday_entry_window = (weekday == 4 and 18 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Technical trend & momentum hygiene
        sma24 = self._sma(closes, 24)
        sma8 = self._sma(closes, 8)
        rsi = self._rsi(closes, 14)

        if sma24 is None or sma8 is None or rsi is None:
            return None

        # Reject entry if market is severely overbought or crashing in crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN") or rsi > 70.0:
            return None

        # Base technical confirmation: short-term trend positive or RSI above oversold floor
        if current_price < sma24 * 0.96 or rsi < 35.0:
            return None

        # Compute conviction confidence
        confidence = 0.65
        if current_price > sma24:
            confidence += 0.15
        if sma8 > sma24:
            confidence += 0.10
        confidence = min(confidence, 0.95)

        self.entry_price = current_price
        self.entry_bar = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=450.0,
            take_profit_bps=750.0,
            horizon_seconds=172800,
            metadata={
                "reason": "friday_weekend_expansion_entry",
                "weekday": weekday,
                "hour": hour,
                "rsi": round(rsi, 2),
                "sma24": round(sma24, 2),
                "price": round(current_price, 2),
                "regime": regime
            }
        )