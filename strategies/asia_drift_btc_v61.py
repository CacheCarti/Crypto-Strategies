from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcAsiaDriftContinuation(Strategy):
    METADATA = {
        "name": "BtcAsiaDriftContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 40,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.entry_bar_idx = -1
        self.cooldown_bars = 4
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.current_day = -1

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        if len(closes) < 35:
            return None

        ts = ctx.bar.timestamp
        current_day = ts.timetuple().tm_yday
        hour = ts.hour

        # Reset daily trackers
        if current_day != self.current_day:
            self.current_day = current_day
            self.asia_open_price = None
            self.asia_close_price = None

        # Track Asia Open (UTC 00:00)
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Record Asia Close / London reference at UTC 08:00
        if hour == 8:
            self.asia_close_price = ctx.bar.close

        # Position Management: Exit conditions
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            # Exit before next Asia session (UTC 22:00 or 23:00) or after holding 7-8 bars (~7-8 hours)
            if hour in (22, 23) or bars_held >= 8:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "session_close_pre_asia" if hour in (22, 23) else "max_hold_reached",
                        "bars_held": bars_held,
                        "hour": hour,
                        "close": ctx.bar.close
                    }
                )
            return None

        # Cooldown guard: ensure spacing after prior position exit
        if ctx.bar_index - self.entry_bar_idx < self.cooldown_bars and self.entry_bar_idx != -1:
            return None

        # Gated to US session early open: UTC 13:00 to 15:00
        if hour not in (13, 14, 15):
            return None

        # Exactly 1 trade attempted per calendar day
        if self.last_trade_day == current_day:
            return None

        # Needs valid Asia session reference prices
        if self.asia_open_price is None or self.asia_close_price is None:
            return None

        # Asia session drift calculation
        asia_drift_bps = ((self.asia_close_price - self.asia_open_price) / self.asia_open_price) * 10000.0

        # Require a clear positive drift during Asia (> 25 bps) but not overextended (< 250 bps)
        if not (25.0 <= asia_drift_bps <= 250.0):
            return None

        # Technical Confirmation: Trend baseline and momentum
        ema20 = self._ema(closes, 20)
        if ema20 is None or ctx.bar.close < ema20:
            return None

        # Regime filter: Skip extreme crisis regimes
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50.0)
        if fear_greed < 20.0:  # Avoid trading long into extreme panic selloffs
            return None

        # Volatility-adjusted dynamic stop / target scaling
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, 14)
        atr_bps = (atr / ctx.bar.close * 10000.0) if (atr and ctx.bar.close > 0) else 100.0

        sl_bps = max(140.0, min(300.0, atr_bps * 1.5))
        tp_bps = max(220.0, min(480.0, atr_bps * 2.4))

        # Confidence scaled by drift strength and alignment
        drift_norm = min(1.0, max(0.5, asia_drift_bps / 120.0))
        confidence = round(0.55 + 0.35 * drift_norm, 2)

        self.last_trade_day = current_day
        self.entry_bar_idx = ctx.bar_index

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=25200,
            metadata={
                "reason": "asia_positive_drift_us_continuation",
                "asia_drift_bps": round(asia_drift_bps, 1),
                "hour": hour,
                "ema20": round(ema20, 2),
                "fear_greed": fear_greed,
                "atr_bps": round(atr_bps, 1)
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.entry_bar_idx = ctx.bar_index