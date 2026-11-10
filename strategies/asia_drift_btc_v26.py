from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 25200,  # ~7 hours (holding from UTC 13 to UTC 20)
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_day = -1
        self.asia_open_price = None
        self.asia_close_price = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(35)
        if len(closes) < 35:
            return None

        current_dt = ctx.bar.timestamp
        current_hour = current_dt.hour
        current_day = current_dt.toordinal()

        # Record Asia session open (UTC 00:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Record Asia session close / drift evaluation point (UTC 08:00)
        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Close position before next Asia session begins (UTC 21:00)
        if ctx.has_position() and current_hour >= 21:
            return ctx.signal(
                "flat",
                confidence=0.6,
                metadata={
                    "reason": "session_close_pre_asia",
                    "hour": current_hour,
                    "close": ctx.bar.close
                }
            )

        # Entry window: Early US session open (UTC 13:00 to 14:00)
        if not ctx.has_position() and self.last_traded_day != current_day:
            if current_hour in (13, 14):
                if self.asia_open_price is not None and self.asia_close_price is not None:
                    # Calculate Asia drift in bps
                    drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0
                    
                    # Trend filter: ensure overall trend aligns (EMA 24)
                    ema24 = self._ema(closes, 24)
                    
                    # Meaningful positive drift (> 25 bps) and trend alignment
                    if drift_bps > 25.0 and ema24 is not None and ctx.bar.close > ema24:
                        # Avoid entering in crisis / high volatility regimes
                        market_regime = ctx.market.get("regime", "NORMAL")
                        if market_regime not in ("CRISIS", "MELTDOWN"):
                            self.last_traded_day = current_day
                            confidence = min(0.85, 0.55 + (drift_bps / 1000.0))
                            
                            return ctx.signal(
                                "long",
                                confidence=round(confidence, 2),
                                stop_loss_bps=220.0,
                                take_profit_bps=400.0,
                                horizon_seconds=25200,
                                metadata={
                                    "reason": "positive_asia_drift_continuation",
                                    "drift_bps": round(drift_bps, 2),
                                    "asia_open": self.asia_open_price,
                                    "asia_close": self.asia_close_price,
                                    "ema24": round(ema24, 2),
                                    "hour": current_hour
                                }
                            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        pass