from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftBtc(Strategy):
    METADATA = {
        "name": "AsiaDriftBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from 13:00 to 20:00 UTC)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.current_day: Optional[int] = None
        self.traded_today: bool = False
        self.last_exit_bar: int = -50
        self.cooldown_bars: int = 4

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        ts = ctx.bar.timestamp
        current_day = ts.day
        hour = ts.hour

        # New calendar day reset
        if self.current_day != current_day:
            self.current_day = current_day
            self.asia_open_price = None
            self.asia_close_price = None
            self.traded_today = False

        # Track Asia session boundaries (00:00 to 08:00 UTC)
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        if hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close position before next Asia session or at end of US session (21:00 UTC)
        if ctx.has_position():
            if hour >= 21 or hour < 12:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_end_of_us", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard after recent exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # US Session Open trigger window: 13:00 to 14:00 UTC, max 1 trade per day
        if 13 <= hour <= 14 and not self.traded_today:
            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_return_pct = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 100.0
                
                # Dynamic filter: trend baseline and regime check
                ema24 = self._ema(closes, 24)
                regime = ctx.market.get("regime", "NORMAL")
                fear_greed = ctx.features.get("fear_greed_index", 50)
                
                # Only take high conviction positive Asia continuation above baseline
                if asia_return_pct >= 0.25 and (ema24 is not None and ctx.bar.close > ema24):
                    if regime not in ("CRISIS", "MELTDOWN") and fear_greed > 25:
                        self.traded_today = True
                        confidence = min(0.85, 0.55 + (asia_return_pct / 4.0))
                        
                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            horizon_seconds=self.METADATA["declared_hold_seconds"],
                            metadata={
                                "reason": "positive_asia_drift_us_continuation",
                                "asia_return_pct": round(asia_return_pct, 3),
                                "asia_open": self.asia_open_price,
                                "asia_close": self.asia_close_price,
                                "ema24": round(ema24, 2) if ema24 else None,
                                "hour": hour,
                                "fear_greed": fear_greed
                            }
                        )

        return None