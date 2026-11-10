from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcAsiaDriftSession(Strategy):
    METADATA = {
        "name": "BtcAsiaDriftSession",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold until close
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_trade_date: Optional[int] = None
        self.current_day: Optional[int] = None
        self.cooldown_bars = 4
        self.bars_since_close = 10

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.bars_since_close = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_close += 1
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < 30:
            return None

        ts = ctx.bar.timestamp
        current_day = ts.toordinal()
        current_hour = ts.hour
        close_price = ctx.bar.close

        # Track session calendar
        if self.current_day != current_day:
            self.current_day = current_day
            self.asia_open_price = None
            self.asia_close_price = None

        # Capture Asia session open (UTC 0:00)
        if current_hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Capture Asia session close / London handoff (UTC 8:00)
        if current_hour == 8 and self.asia_open_price is not None:
            self.asia_close_price = close_price

        # Mandatory exit: flat before the next Asia session begins (UTC 23:00)
        if ctx.has_position():
            if current_hour >= 23 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "session_close_pre_asia", "hour": current_hour, "price": close_price}
                )
            return None

        # Entry logic: Evaluated strictly at the US open transition window (UTC 13:00 - 14:00)
        if 13 <= current_hour <= 14:
            if self.last_trade_date == current_day:
                return None
            if self.bars_since_close < self.cooldown_bars:
                return None
            if self.asia_open_price is None or self.asia_close_price is None:
                return None

            # Calculate drift across UTC 0-8
            asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

            # Filter: Require positive drift of at least 30 bps to avoid flat chop
            if asia_drift_bps < 30.0:
                return None

            # Confirmation 1: Volatility filter (avoid entry in extreme breakdown/crisis)
            atr_val = self._atr(highs, lows, closes, 14)
            if atr_val is None:
                return None
            norm_vol_bps = (atr_val / close_price) * 10000.0

            regime = ctx.market.get("regime", "NORMAL")
            if regime in ["CRISIS", "MELTDOWN"]:
                return None

            # Confirmation 2: Continuous drift into US open (US open price >= Asia close)
            # Confirms continuation rather than immediate London mean reversion
            if close_price < self.asia_close_price:
                return None

            # Confirmation 3: Funding rate sanity check (do not chase extreme crowded longs)
            funding = ctx.features.get("funding_rate_btcusdt", 0.0)
            if funding > 0.0006:  # > 0.06% per 8h is heavily overextended
                return None

            self.last_trade_date = current_day

            # Dynamic SL/TP based on ATR
            sl_bps = max(180.0, min(norm_vol_bps * 1.5, 300.0))
            tp_bps = max(300.0, min(norm_vol_bps * 2.8, 500.0))
            conf = min(0.85, max(0.55, 0.5 + (asia_drift_bps / 500.0)))

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=36000,
                metadata={
                    "reason": "asia_drift_us_continuation",
                    "asia_drift_bps": round(asia_drift_bps, 2),
                    "norm_vol_bps": round(norm_vol_bps, 2),
                    "funding_rate": funding,
                    "hour": current_hour,
                    "price": close_price
                }
            )

        return None