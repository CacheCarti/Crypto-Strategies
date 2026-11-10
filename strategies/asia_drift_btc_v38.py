from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold max
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.current_day: Optional[int] = None
        self.traded_today: bool = False
        self.bars_since_exit: int = 10
        self.min_drift_bps: float = 30.0  # require at least +30 bps drift in Asia to confirm momentum
        self.max_drift_bps: float = 350.0  # avoid exhausted parabolic moves

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
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(50)
        highs = ctx.highs(20)
        lows = ctx.lows(20)

        if len(closes) < 50:
            return None

        ts = ctx.bar.timestamp
        current_day = ts.day
        hour = ts.hour

        # Reset daily tracking at the start of a new calendar day
        if self.current_day != current_day:
            self.current_day = current_day
            self.traded_today = False
            self.asia_open_price = None

        # Capture open of Asia session at 00:00 UTC
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Exit mandatory window: close position prior to next Asia session (UTC 22-23)
        if ctx.has_position():
            if hour >= 22 or hour == 23:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_pre_asia",
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Entry window: US Session opening / pre-US overlap (13:00 - 15:00 UTC)
        # Allows entering once per day if Asia drift conditions are met
        if not self.traded_today and self.bars_since_exit >= 4 and (13 <= hour <= 15):
            if self.asia_open_price is not None and self.asia_open_price > 0:
                asia_drift_bps = ((ctx.bar.close - self.asia_open_price) / self.asia_open_price) * 10000.0

                # Must show positive continuation drift, not overextended
                if self.min_drift_bps <= asia_drift_bps <= self.max_drift_bps:
                    ema20 = self._ema(closes, 20)
                    ema50 = self._ema(closes, 50)

                    # Price should be tracking near or above trend EMA
                    if ema20 is not None and ema50 is not None and ctx.bar.close > ema50:
                        # ATR-adjusted SL/TP calculation
                        atr = self._atr(highs, lows, closes, 14)
                        vol_mult = (atr / ctx.bar.close * 10000.0) if (atr and ctx.bar.close > 0) else 100.0
                        
                        sl_bps = max(150.0, min(300.0, vol_mult * 1.5))
                        tp_bps = max(250.0, min(500.0, vol_mult * 2.5))
                        
                        # Market regime filter: avoid entering during extreme crisis
                        crisis = ctx.market.get("crisis_score", 0.0)
                        if crisis > 0.7:
                            return None

                        conf = min(0.9, max(0.5, 0.5 + (asia_drift_bps / 500.0)))
                        self.traded_today = True

                        return ctx.signal(
                            "long",
                            confidence=conf,
                            stop_loss_bps=sl_bps,
                            take_profit_bps=tp_bps,
                            horizon_seconds=28800,
                            metadata={
                                "reason": "asia_drift_us_continuation",
                                "drift_bps": round(asia_drift_bps, 2),
                                "asia_open": self.asia_open_price,
                                "close": ctx.bar.close,
                                "ema20": round(ema20, 2),
                                "ema50": round(ema50, 2),
                                "hour": hour
                            }
                        )

        return None