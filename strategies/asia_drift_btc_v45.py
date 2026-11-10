from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUSContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold from US open to Asia start
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_start_hour = 0
        self.asia_end_hour = 8
        self.us_entry_hour = 13  # 13:00 UTC (US equity open/pre-market overlap)
        self.asia_close_hour = 23  # Exit before new Asia session
        
        self.current_day = None
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.traded_today = False
        self.last_exit_bar = -100

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period: int = 14):
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
        if len(closes) < 40:
            return None

        bar_time = ctx.bar.timestamp
        current_hour = bar_time.hour
        today = bar_time.date()

        # New day reset
        if self.current_day != today:
            self.current_day = today
            self.asia_open_price = None
            self.asia_close_price = None
            self.traded_today = False

        # Capture Asia session boundaries
        if current_hour == self.asia_start_hour and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        if current_hour == self.asia_end_hour:
            self.asia_close_price = ctx.bar.close

        # Mandatory exit: Close before the next Asia session
        if ctx.has_position():
            if current_hour >= self.asia_close_hour or current_hour < self.asia_start_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": ctx.bar.close}
                )
            return None

        # Cooldown guard: Wait at least 6 bars (~6 hours) after any exit
        if ctx.bar_index - self.last_exit_bar < 6:
            return None

        # Entry logic: Only evaluate at the US session trigger hour
        if current_hour == self.us_entry_hour and not self.traded_today and not ctx.has_position():
            # Check market regime to avoid crisis / breakdown environments
            market_regime = ctx.market.get("regime", "NORMAL")
            if market_regime in ("CRISIS", "MELTDOWN"):
                return None

            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0
                
                # High conviction entry: Asia drift must be clearly positive (> 40 bps) to filter noise
                # But not excessively extended (< 250 bps)
                if 40.0 < asia_drift_bps < 250.0:
                    highs = ctx.highs(20)
                    lows = ctx.lows(20)
                    atr = self._atr(highs, lows, closes, 14)
                    sma_24 = self._sma(closes, 24)

                    # Ensure price is above 24h baseline (trend alignment)
                    if sma_24 and ctx.bar.close > sma_24:
                        self.traded_today = True
                        confidence = min(0.85, 0.55 + (asia_drift_bps / 500.0))
                        
                        return ctx.signal(
                            "long",
                            confidence=confidence,
                            stop_loss_bps=220.0,
                            take_profit_bps=400.0,
                            horizon_seconds=28800,
                            metadata={
                                "reason": "asia_drift_us_continuation",
                                "asia_drift_bps": round(asia_drift_bps, 2),
                                "sma24": round(sma_24, 2),
                                "atr": round(atr, 2) if atr else 0.0,
                                "price": ctx.bar.close,
                                "regime": market_regime
                            }
                        )

        return None