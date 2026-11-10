from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUSContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUSContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold from US open to Asia onset
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_start_hour = 0
        self.asia_end_hour = 8
        self.us_entry_hour = 13  # 13:00 UTC (US equity open drift / early cash overlap)
        self.exit_hour = 23      # Close before UTC 00:00 next Asia session
        
        self.current_day = -1
        self.traded_today = False
        self.asia_open_price: Optional[float] = None
        self.asia_drift_bps: Optional[float] = None
        self.bars_since_exit = 10

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
        self.bars_since_exit = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(40)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        bar = ctx.bar
        ts = bar.timestamp
        current_hour = ts.hour
        day_of_year = ts.timetuple().tm_yday

        # Reset day tracker
        if day_of_year != self.current_day:
            self.current_day = day_of_year
            self.traded_today = False
            self.asia_open_price = None
            self.asia_drift_bps = None

        # Capture Asia session open (UTC 00:00)
        if current_hour == self.asia_start_hour and self.asia_open_price is None:
            self.asia_open_price = bar.open

        # Record Asia drift at end of Asia session (UTC 08:00)
        if current_hour == self.asia_end_hour and self.asia_open_price is not None:
            self.asia_drift_bps = ((bar.close - self.asia_open_price) / self.asia_open_price) * 10000.0

        # Mandatory session exit before next Asia open
        if ctx.has_position():
            if current_hour >= self.exit_hour or current_hour < self.asia_end_hour:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_pre_asia",
                        "hour": current_hour,
                        "close": bar.close,
                    }
                )
            return None

        # Gating: strictly one trade per day, cooldown respected, no open position
        if self.traded_today or self.bars_since_exit < 4:
            return None

        # Entry window: exactly at US session arrival (13:00 UTC)
        if current_hour == self.us_entry_hour and self.asia_drift_bps is not None:
            # Avoid repeating failed low-conviction drift trades:
            # Asia drift must show decisive momentum (> 35 bps), but not overextended exhaustion (< 250 bps)
            if 35.0 <= self.asia_drift_bps <= 250.0:
                # Selective confirmation: Price must hold above 24-bar SMA (1-day trend alignment)
                sma24 = self._sma(closes, 24)
                if sma24 is not None and bar.close > sma24:
                    # Check market volatility regime; avoid meltdowns
                    regime = ctx.market.get("regime", "NORMAL")
                    if regime in ["NORMAL", "calm", "normal"]:
                        self.traded_today = True
                        
                        # Scale confidence with drift clarity
                        norm_conf = min(0.85, max(0.55, 0.5 + (self.asia_drift_bps / 500.0)))
                        
                        return ctx.signal(
                            "long",
                            confidence=norm_conf,
                            stop_loss_bps=self.METADATA["declared_sl_bps"],
                            take_profit_bps=self.METADATA["declared_tp_bps"],
                            metadata={
                                "reason": "asia_drift_continuation_us_open",
                                "asia_drift_bps": round(self.asia_drift_bps, 2),
                                "sma24": round(sma24, 2),
                                "price": round(bar.close, 2),
                                "regime": regime,
                            }
                        )

        return None