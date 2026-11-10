from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from UTC 13 to UTC 20/21)
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_day: Optional[int] = None
        self.bars_since_exit: int = 24

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_of_year = current_time.timetuple().tm_yday

        # Track Asia session (UTC 00:00 to 08:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open
        elif hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close position prior to next day Asia session (e.g. UTC 21:00 or 22:00)
        if ctx.has_position():
            if hour >= 21 or hour < 8:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_asia", "hour": hour, "close": ctx.bar.close}
                )
            return None

        # Entry window: US session open (UTC 13:00 to 14:00)
        # Ensure 1 trade per day maximum and strict cooldown
        if hour in (13, 14) and self.last_trade_day != day_of_year and self.bars_since_exit >= 5:
            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_return_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0
                
                # Check macro trend filter: 30-period EMA
                ema30 = self._ema(closes, 30)
                fear_greed = ctx.features.get("fear_greed_index", 50)
                crisis_score = ctx.market.get("crisis_score", 0.0)

                # Bullish Asia drift continuation into US open:
                # Requires Asia gain >= 35 bps, price above EMA30, avoiding extreme panic
                if asia_return_bps >= 35.0 and ema30 is not None and ctx.bar.close > ema30 and crisis_score < 0.6:
                    confidence = 0.65
                    if fear_greed >= 45:
                        confidence += 0.15
                    if ctx.bar.close > ctx.bar.open:
                        confidence += 0.10
                    confidence = min(0.95, confidence)

                    self.last_trade_day = day_of_year
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_drift_bullish_continuation",
                            "asia_return_bps": round(asia_return_bps, 2),
                            "ema30": round(ema30, 2),
                            "close": ctx.bar.close,
                            "fear_greed": fear_greed,
                            "hour": hour
                        }
                    )

        return None