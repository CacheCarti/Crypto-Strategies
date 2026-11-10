from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_trade_date: Optional[str] = None
        self.last_exit_bar: int = -50
        self.cooldown_bars: int = 4
        self.min_drift_bps: float = 35.0
        self.max_drift_bps: float = 280.0

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
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        today_str = current_time.strftime("%Y-%m-%d")

        # Track Asia Open (00:00 UTC)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Regime and indicator context
        regime = ctx.market.get("regime", "NORMAL")
        trend_regime = ctx.market.get("trend_regime", "neutral")
        ema30 = self._ema(closes, 30)
        rsi = self._rsi(closes, 14)
        current_close = ctx.bar.close

        # Position exit rule: close before the next Asia session or if trend deteriorates
        if ctx.has_position():
            if current_hour >= 23 or current_hour == 0:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": current_close}
                )
            if rsi is not None and rsi > 76.0:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_overbought_exhaustion", "rsi": round(rsi, 2), "price": current_close}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Limit to 1 entry per day
        if self.last_trade_date == today_str:
            return None

        # Entry window: US session open (13:00 - 15:00 UTC)
        if 13 <= current_hour <= 15:
            if self.asia_open_price is None or self.asia_open_price <= 0:
                return None

            if regime in ("CRISIS", "MELTDOWN"):
                return None

            drift_bps = ((current_close - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Qualified Asia positive drift continuation setup
            if self.min_drift_bps <= drift_bps <= self.max_drift_bps:
                if ema30 is not None and current_close > ema30:
                    if rsi is not None and 42.0 <= rsi <= 68.0:
                        self.last_trade_date = today_str
                        confidence = 0.65
                        if trend_regime == "bull":
                            confidence += 0.15

                        return ctx.signal(
                            "long",
                            confidence=min(confidence, 0.95),
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "asia_positive_drift_us_open_continuation",
                                "drift_bps": round(drift_bps, 2),
                                "asia_open": self.asia_open_price,
                                "rsi": round(rsi, 2),
                                "ema30": round(ema30, 2),
                                "hour": current_hour,
                            }
                        )

        return None