from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class WeekendSeasonalityETH(Strategy):
    METADATA = {
        "name": "WeekendSeasonalityETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold over the weekend
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 12
        self.last_exit_bar = -100

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
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
        closes = ctx.closes(self.trend_period + 5)
        if len(closes) < self.trend_period + 1:
            return None

        # Check market regime to avoid crisis / meltdown liquidity drains
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "exit_regime_meltdown", "regime": market_regime}
                )
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()  # Monday=0, Friday=4, Saturday=5, Sunday=6
        hour = dt.hour

        # Exit logic: late Sunday (>= 20 UTC) or early Monday (< 4 UTC)
        if ctx.has_position():
            is_weekend_close = (weekday == 6 and hour >= 20) or (weekday == 0 and hour < 4)
            if is_weekend_close:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "weekend_drift_window_end",
                        "weekday": weekday,
                        "hour": hour,
                        "price": ctx.bar.close
                    }
                )
            return None

        # Entry logic: strictly Friday evening (16:00 to 23:00 UTC)
        is_friday_entry_window = (weekday == 4 and 16 <= hour <= 23)
        if not is_friday_entry_window:
            return None

        # Enforce cooldown after prior exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        sma30 = self._sma(closes, self.trend_period)
        rsi14 = self._rsi(closes, self.rsi_period)
        if sma30 is None or rsi14 is None:
            return None

        current_close = ctx.bar.close

        # Edge gating: avoid buying deep downtrends or overextended euphoric moves
        # Long bias holds edge when price maintains trend sanity and RSI is balanced
        trend_aligned = current_close >= sma30 * 0.985
        not_overbought = rsi14 <= 66.0
        not_crashing = rsi14 >= 32.0

        if trend_aligned and not_overbought and not_crashing:
            confidence = 0.65
            if current_close > sma30:
                confidence += 0.15
            if 40.0 <= rsi14 <= 58.0:
                confidence += 0.10

            return ctx.signal(
                "long",
                confidence=min(confidence, 1.0),
                stop_loss_bps=450.0,
                take_profit_bps=750.0,
                horizon_seconds=172800,
                metadata={
                    "reason": "friday_evening_seasonal_drift_entry",
                    "weekday": weekday,
                    "hour": hour,
                    "price": current_close,
                    "sma30": round(sma30, 2),
                    "rsi14": round(rsi14, 2)
                }
            )

        return None