from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 750.0,
        "declared_hold_seconds": 172800,  # ~48 hours hold across weekend
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.entry_weekday = 4      # Friday (0=Mon, 4=Fri, 6=Sun)
        self.entry_hour_start = 18  # 18:00 UTC Friday
        self.entry_hour_end = 23    # 23:00 UTC Friday
        self.exit_weekday = 6       # Sunday
        self.exit_hour = 20         # 20:00 UTC Sunday
        self.last_exit_bar = -100
        self.cooldown_bars = 48     # Hard cooldown (48 hours = 2 days minimum)
        self.last_entry_bar = -100

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        # Check market regime guard: avoid entering or holding through extreme crises
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "exit_crisis_regime", "regime": market_regime, "bar": ctx.bar_index}
                )
            return None

        dt = ctx.bar.timestamp
        weekday = dt.weekday()
        hour = dt.hour

        # Check exit conditions if in a long position
        if ctx.has_position():
            # Time-based boundary exit: Sunday at or after target exit hour
            if weekday == self.exit_weekday and hour >= self.exit_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "weekend_window_expired",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )

            # Defensive exit if held into Monday morning
            if weekday == 0 and hour >= 0:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "monday_cutoff_exit",
                        "weekday": weekday,
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )

            return None

        # Cooldown guard: must not enter if an exit happened recently
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Entry calendar window: Friday between 18:00 and 23:00 UTC
        if weekday == self.entry_weekday and (self.entry_hour_start <= hour <= self.entry_hour_end):
            # Only trigger once per weekend cycle
            if ctx.bar_index - self.last_entry_bar < 72:
                return None

            # Price action confirmation: not in severe downtrend, not heavily overbought
            sma30 = self._sma(closes, 30)
            rsi = self._rsi(closes, 14)
            fear_greed = ctx.features.get("fear_greed_index", 50.0)

            if sma30 is None or rsi is None:
                return None

            # Filter: price not drastically collapsed below SMA (>4% below SMA) and RSI not blown out (>72)
            pct_from_sma = (ctx.bar.close - sma30) / sma30
            if pct_from_sma < -0.04 or rsi > 72.0:
                return None

            # Confidence scaled by trend alignment and sentiment
            confidence = 0.65
            if pct_from_sma >= 0:
                confidence += 0.1
            if fear_greed >= 40:
                confidence += 0.1

            confidence = min(0.9, max(0.5, confidence))
            self.last_entry_bar = ctx.bar_index

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
                    "pct_from_sma": round(pct_from_sma, 4),
                    "fear_greed": fear_greed,
                    "close": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index