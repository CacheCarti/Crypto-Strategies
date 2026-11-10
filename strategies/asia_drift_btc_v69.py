from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUSContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours (hold across US session into early Asia)
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.last_traded_day: Optional[int] = None
        self.bars_since_exit: int = 10

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        current_day = bar_time.toordinal()

        # Mark Asia session reference open at 00:00 UTC
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open

        # Close positions before next Asia open (at 22:00 or 23:00 UTC)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "end_of_us_session_pre_asia",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Mandatory cooldown after any exit
        if self.bars_since_exit < 4:
            return None

        # Gate to US session open: trigger entry evaluation at 13:00 or 14:00 UTC
        if current_hour not in (13, 14):
            return None

        # Enforce maximum 1 trade per calendar day
        if self.last_traded_day == current_day:
            return None

        if self.asia_open_price is None or self.asia_open_price <= 0:
            return None

        # Dynamic volatility-adjusted drift threshold instead of rigid magic numbers
        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr <= 0:
            return None

        drift = ctx.bar.close - self.asia_open_price
        drift_atr_ratio = drift / atr

        # Trend filter: ensure medium-term regime is supportive (above SMA 30)
        sma30 = self._sma(closes, 30)
        if sma30 is None:
            return None

        # Regime safety checks
        fg_index = ctx.features.get("fear_greed_index", 50)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN") or fg_index < 20:
            return None

        # State check: positive drift during Asia session exceeding 0.35 ATR
        # and price closing above dynamic trend baseline
        if drift_atr_ratio >= 0.35 and ctx.bar.close > sma30:
            self.last_traded_day = current_day
            confidence = min(0.85, max(0.55, 0.5 + 0.15 * min(drift_atr_ratio, 2.0)))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "asia_positive_drift_us_continuation",
                    "drift_atr_ratio": round(drift_atr_ratio, 3),
                    "asia_open": round(self.asia_open_price, 2),
                    "close": round(ctx.bar.close, 2),
                    "atr": round(atr, 2),
                    "sma30": round(sma30, 2),
                    "fear_greed": fg_index,
                }
            )

        return None