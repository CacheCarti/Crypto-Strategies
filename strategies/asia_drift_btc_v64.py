from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until Asia onset
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_drift_pct: float = 0.0
        self.last_trade_day: Optional[int] = None
        self.cooldown_until_bar: int = 0
        self.min_drift_bps: float = 35.0  # Require clear Asia session directional expansion
        self.max_drift_bps: float = 350.0 # Avoid exhausted blow-offs

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until_bar = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_id = current_time.toordinal()

        # Track Asia Session (UTC 00:00 to 08:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        # Record drift at the end of the Asia session (UTC 08:00)
        if hour == 8 and self.asia_open_price is not None and self.asia_open_price > 0:
            self.asia_drift_pct = (ctx.bar.close - self.asia_open_price) / self.asia_open_price

        # Mandatory exit before the start of the next Asia session (UTC 23:00)
        if ctx.has_position():
            if hour >= 23 or hour < 8:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Guard against trading during cooldown
        if ctx.bar_index < self.cooldown_until_bar:
            return None

        # Execute at US market open window (UTC 13:00 - 15:00), max 1 trade per calendar day
        if 13 <= hour <= 15:
            if self.last_trade_day == day_id:
                return None

            drift_bps = self.asia_drift_pct * 10000.0

            # Condition 1: Asia demonstrated moderate, non-exhausted bullish drift
            if self.min_drift_bps <= drift_bps <= self.max_drift_bps:
                ema20 = self._ema(closes, 20)
                if ema20 is None:
                    return None

                # Confirmation: Price maintains above EMA20 and not in crisis regime
                regime = ctx.market.get("regime", "NORMAL")
                if ctx.bar.close > ema20 and regime not in ["CRISIS", "MELTDOWN"]:
                    highs = ctx.highs(20)
                    lows = ctx.lows(20)
                    atr = self._atr(highs, lows, closes, 14)
                    atr_val = atr if atr is not None else (ctx.bar.close * 0.01)

                    # Dynamic stop based on current volatility
                    atr_bps = (atr_val / ctx.bar.close) * 10000.0
                    sl_bps = max(160.0, min(300.0, atr_bps * 1.5))
                    tp_bps = max(300.0, min(600.0, sl_bps * 1.8))

                    fear_greed = ctx.features.get("fear_greed_index", 50)
                    confidence = 0.65 if fear_greed >= 40 else 0.5

                    self.last_trade_day = day_id
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_bullish_drift_us_open_continuation",
                            "asia_drift_bps": round(drift_bps, 2),
                            "ema20": round(ema20, 2),
                            "fear_greed": fear_greed,
                            "regime": regime,
                            "hour": hour
                        }
                    )

        return None