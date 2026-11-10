from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift US Session Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.current_date: Optional[str] = None
        self.traded_today: bool = False
        self.last_exit_bar: int = -50

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        today_str = bar_time.strftime("%Y-%m-%d")

        # New calendar day reset
        if self.current_date != today_str:
            self.current_date = today_str
            self.asia_open_price = None
            self.traded_today = False

        # Capture Asia session open reference at UTC 00:00
        if current_hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        current_price = ctx.bar.close

        # Mandatory session exit: flatten before Asia opens (hour 23)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 2:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "pre_asia_session_close",
                        "hour": current_hour,
                        "price": current_price
                    }
                )
            return None

        # Cooldown guard: require at least 6 bars (~6 hours) after any prior exit
        if ctx.bar_index - self.last_exit_bar < 6:
            return None

        # Only evaluate entries right at the US open transition (UTC 13:00 to 15:00)
        if current_hour not in (13, 14, 15):
            return None

        if self.traded_today or self.asia_open_price is None:
            return None

        # Regime and market state confirmation to fix prior OOS failure
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.40:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed < 25.0:
            # Avoid longing continuation into severe macro panic
            return None

        # Volatility normalization: ensure Asia drift is substantial relative to ATR
        highs = ctx.highs(25)
        lows = ctx.lows(25)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        drift_bps = ((current_price - self.asia_open_price) / self.asia_open_price) * 10000.0
        drift_in_atr = (current_price - self.asia_open_price) / atr

        # Require a clean, distinct positive drift (at least 35 bps and 0.5 ATR)
        if drift_bps > 35.0 and drift_in_atr > 0.5:
            # Scaled confidence based on drift strength
            confidence = min(0.85, 0.55 + (drift_bps / 300.0) * 0.25)
            self.traded_today = True

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "asia_positive_drift_us_continuation",
                    "drift_bps": round(drift_bps, 2),
                    "drift_in_atr": round(drift_in_atr, 2),
                    "asia_open": self.asia_open_price,
                    "fear_greed": fear_greed,
                    "atr": round(atr, 2)
                }
            )

        return None