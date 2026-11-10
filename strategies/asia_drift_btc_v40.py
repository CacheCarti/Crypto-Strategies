from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any

class AsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "Asia Drift Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 25200,  # ~7 hours (from US open to Asia close)
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.current_trade_day: Optional[int] = None
        self.traded_today: bool = False
        self.min_drift_bps: float = 30.0   # Require clear positive drift (>0.30%)
        self.max_drift_bps: float = 350.0  # Avoid overextended / exhausted moves

    def _ema(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        bar_time = ctx.bar.timestamp
        current_day = bar_time.toordinal()
        current_hour = bar_time.hour
        close_price = ctx.bar.close

        # Daily state reset at start of UTC day
        if self.current_trade_day != current_day:
            self.current_trade_day = current_day
            self.traded_today = False
            self.asia_open_price = ctx.bar.open

        # Record Asia open reference price at 00:00 UTC
        if current_hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Exit condition: close existing position at end of US/London session before next Asia session (hour 23)
        if ctx.has_position():
            if current_hour >= 23 or current_hour == 0:
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "session_close_pre_asia",
                    "hour": current_hour,
                    "price": close_price
                })
            return None

        # Entry trigger: strictly at US market open (hour 13 or 14 UTC) to prevent repeated signals
        # One trade per calendar day enforced via self.traded_today
        if current_hour in (13, 14) and not self.traded_today and not ctx.has_position():
            if self.asia_open_price is not None and self.asia_open_price > 0:
                # Measure drift across the completed Asia session (00:00 - 08:00 UTC)
                drift_bps = ((close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

                # Trend filter: ensure overall daily momentum is aligned (close above 24h EMA)
                ema24 = self._ema(closes, 24)

                # Regime filter from market context
                regime = ctx.market.get("regime", "NORMAL")

                if (self.min_drift_bps <= drift_bps <= self.max_drift_bps and
                    ema24 is not None and close_price > ema24 and
                    regime not in ("CRISIS", "MELTDOWN")):

                    self.traded_today = True

                    # Confidence scaled based on drift magnitude
                    confidence = 0.60 + min(0.25, (drift_bps - self.min_drift_bps) / 400.0)

                    return ctx.signal(
                        "long",
                        confidence=round(confidence, 2),
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "asia_drift_continuation_entry",
                            "drift_bps": round(drift_bps, 1),
                            "asia_open": round(self.asia_open_price, 2),
                            "current_price": round(close_price, 2),
                            "ema24": round(ema24, 2),
                            "hour": current_hour
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Mark as traded for the day to ensure max one trade per day strictness
        self.traded_today = True