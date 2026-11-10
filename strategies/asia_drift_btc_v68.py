from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftMomentum(Strategy):
    METADATA = {
        "name": "AsiaDriftMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until end of US session
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_traded_date = None
        self.asia_open_price = None
        self.last_exit_bar = -999
        self.cooldown_bars = 4

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

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
        closes = ctx.closes(30)
        if len(closes) < 30:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_date = current_time.date()

        # Track session opening price at 00:00 UTC (Asia start)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Exit condition: End of NY session (22:00 or 23:00 UTC) before next Asia session
        if ctx.has_position():
            if current_hour >= 22 or current_hour == 23:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "end_of_us_session_exit",
                        "current_hour": current_hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Hard cooldown enforcement
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Entry window: US session open (13:00 to 14:00 UTC)
        # Limit to 1 trade per calendar day
        if 13 <= current_hour <= 14 and self.last_traded_date != current_date:
            if self.asia_open_price is not None and self.asia_open_price > 0:
                asia_return_pct = (ctx.bar.close - self.asia_open_price) / self.asia_open_price

                # Minimum drift threshold: Asia return > +0.25% (25 bps)
                if asia_return_pct >= 0.0025:
                    ema20 = self._ema(closes, 20)
                    highs = ctx.highs(20)
                    lows = ctx.lows(20)
                    atr = self._atr(highs, lows, closes, 14) or (ctx.bar.close * 0.01)

                    # Confirmation: Price must be above EMA20 and market regime not meltdown
                    market_regime = ctx.market.get("regime", "NORMAL")
                    if ema20 is not None and ctx.bar.close > ema20 and market_regime not in ["CRISIS", "MELTDOWN"]:
                        fg_index = ctx.features.get("fear_greed_index", 50)
                        
                        # Scale confidence based on drift strength
                        drift_bps = asia_return_pct * 10000.0
                        confidence = min(0.9, max(0.55, 0.5 + (drift_bps / 500.0) * 0.3))

                        self.last_traded_date = current_date
                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=220.0,
                            take_profit_bps=360.0,
                            horizon_seconds=28800,
                            metadata={
                                "reason": "asia_drift_continuation",
                                "drift_bps": round(drift_bps, 2),
                                "asia_open": self.asia_open_price,
                                "price": ctx.bar.close,
                                "ema20": round(ema20, 2),
                                "atr": round(atr, 2),
                                "fear_greed": fg_index
                            }
                        )

        return None