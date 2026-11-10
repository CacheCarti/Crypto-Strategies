from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from 13:00 to 20:00 UTC)
        "warmup_bars": 50,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_date: Optional[str] = None
        self.last_exit_bar: int = -50

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < 50:
            return None

        dt = ctx.bar.timestamp
        hour = dt.hour
        date_str = dt.strftime("%Y-%m-%d")

        # Track Asia Session (UTC 00:00 to 08:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None

        if hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close position before next Asia session or at end of US session (21:00 UTC)
        if ctx.has_position():
            if hour >= 21 or hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_prior_asia", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Mandatory cooldown check (at least 6 hours / bars since last exit)
        if (ctx.bar_index - self.last_exit_bar) < 6:
            return None

        # Entry window: US market open (13:00 - 15:00 UTC)
        # Strictly one trade attempt per calendar day
        if 13 <= hour <= 15 and self.last_trade_date != date_str:
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate Asia session drift
            asia_drift_pct = (self.asia_close_price - self.asia_open_price) / self.asia_open_price

            # Regime filters: avoid meltdown / extreme crises
            regime = ctx.market.get("regime", "NORMAL")
            if regime in ("CRISIS", "MELTDOWN"):
                return None

            # Positive drift threshold (+0.25% drift during Asia session required)
            if asia_drift_pct >= 0.0025:
                # Confirm US open price is maintaining momentum above Asia close & above 20 EMA
                ema20 = self._ema(closes, 20)
                if ema20 is not None and ctx.bar.close >= self.asia_close_price and ctx.bar.close >= ema20:
                    highs = ctx.highs(20)
                    lows = ctx.lows(20)
                    atr = self._atr(highs, lows, closes, 14)
                    atr_pct = (atr / ctx.bar.close) if atr else 0.01

                    # Dynamic stop & target scaled by volatility
                    sl_bps = max(150.0, min(300.0, atr_pct * 1.5 * 10000.0))
                    tp_bps = sl_bps * 1.7

                    fg = ctx.features.get("fear_greed_index", 50)
                    confidence = 0.65
                    if fg > 50:
                        confidence = min(0.85, confidence + 0.1)

                    self.last_trade_date = date_str

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=sl_bps,
                        take_profit_bps=tp_bps,
                        horizon_seconds=25200,
                        metadata={
                            "reason": "asia_drift_continuation_us_open",
                            "asia_drift_pct": round(asia_drift_pct, 4),
                            "asia_open": self.asia_open_price,
                            "asia_close": self.asia_close_price,
                            "ema20": round(ema20, 2),
                            "price": ctx.bar.close,
                            "hour": hour,
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index