from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthWeekendSeasonality(Strategy):
    METADATA = {
        "name": "EthWeekendSeasonality",
        "domain": "eth_usdc",
        "declared_sl_bps": 550.0,
        "declared_tp_bps": 850.0,
        "declared_hold_seconds": 172800,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 20
        self.slow_ema_period = 50
        self.rsi_period = 14
        self.cooldown_bars = 18
        self.last_exit_bar = -100

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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Filter out extreme crisis regimes to preserve capital
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "crisis_exit", "regime": regime, "crisis_score": crisis_score}
                )
            return None

        ts = ctx.bar.timestamp
        weekday = ts.weekday()
        hour = ts.hour

        # Exit logic: Sunday evening UTC (weekday 6, hours 20-23)
        if ctx.has_position():
            if weekday == 6 and hour >= 20:
                rsi = self._rsi(closes, self.rsi_period)
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "sunday_window_close", "weekday": weekday, "hour": hour, "rsi": rsi}
                )
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry logic: Friday evening window (weekday 4, hours 16-22 UTC)
        if weekday == 4 and (16 <= hour <= 22):
            ema20 = self._ema(closes, self.fast_ema_period)
            ema50 = self._ema(closes, self.slow_ema_period)
            rsi = self._rsi(closes, self.rsi_period)

            if ema20 is None or ema50 is None or rsi is None:
                return None

            current_price = ctx.bar.close

            # High-conviction entry: ETH not structurally broken and not overextended
            # Price must be above medium trend (EMA50) and RSI within reasonable momentum zone
            trend_ok = current_price >= ema50 and ema20 >= (ema50 * 0.995)
            momentum_ok = 42.0 <= rsi <= 68.0

            if trend_ok and momentum_ok:
                confidence = 0.65
                if current_price > ema20:
                    confidence += 0.15

                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "friday_seasonality_long",
                        "price": current_price,
                        "ema20": ema20,
                        "ema50": ema50,
                        "rsi": rsi,
                        "weekday": weekday,
                        "hour": hour
                    }
                )

        return None