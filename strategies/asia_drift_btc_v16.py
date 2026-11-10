from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift US Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 25200,  # ~7 hours (exit at 21:00 UTC)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.asia_open_price = None
        self.asia_close_price = None
        self.cooldown_until = 0

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + 4

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        dt = ctx.bar.timestamp
        hour = dt.hour
        current_date = dt.date()

        # Track Asia session (UTC 0:00 to 8:00)
        if hour == 0:
            self.asia_open_price = ctx.bar.open

        if hour == 8:
            self.asia_close_price = ctx.bar.close

        # Mandatory Exit: Close before the next Asia session at UTC 22:00
        if ctx.has_position() and hour >= 22:
            return ctx.signal("flat", confidence=0.8, metadata={
                "reason": "session_close_pre_asia",
                "hour": hour,
                "price": ctx.bar.close
            })

        # Cooldown guard
        if ctx.bar_index < self.cooldown_until:
            return None

        # Entry logic: US session open window (UTC 13:00 - 15:00)
        if not ctx.has_position() and 13 <= hour <= 15:
            if self.last_traded_date != current_date and self.asia_open_price is not None and self.asia_close_price is not None:
                asia_return_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0
                
                # Check macro alignment using EMA 24 to confirm positive trend
                ema24 = self._ema(closes, 24)
                price_above_ema = ema24 is not None and ctx.bar.close > ema24
                
                # Require positive Asia drift between 25 bps and 350 bps (clean trend, not over-extended)
                if 25.0 <= asia_return_bps <= 350.0 and price_above_ema:
                    self.last_traded_date = current_date
                    confidence = min(0.9, max(0.55, 0.55 + (asia_return_bps / 500.0)))
                    
                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=220.0,
                        take_profit_bps=400.0,
                        metadata={
                            "reason": "asia_positive_drift_us_continuation",
                            "asia_return_bps": round(asia_return_bps, 2),
                            "ema24": round(ema24, 2) if ema24 else 0.0,
                            "close": ctx.bar.close,
                            "hour": hour
                        }
                    )

        return None