from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class AsiaDriftUsContinuation(Strategy):
    METADATA = {
        "name": "AsiaDriftUsContinuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 28800,  # ~8 hours typical hold (13:00 to 21:00 UTC)
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.last_exit_bar = -999

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        current_hour = current_time.hour
        current_day = current_time.toordinal()

        # Track Asia session boundaries (UTC 00:00 to 08:00)
        if current_hour == 0:
            self.asia_open_price = ctx.bar.open
            self.asia_close_price = None

        if current_hour == 8:
            self.asia_close_price = ctx.bar.close

        # Mandatory timed exit: Close before the next Asia session starts (UTC 22:00 or 23:00)
        if ctx.has_position():
            if current_hour >= 22 or current_hour < 1:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "timed_session_exit_before_asia",
                        "hour": current_hour,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Cooldown guard: wait at least 3 bars after exit
        if ctx.bar_index - self.last_exit_bar < 3:
            return None

        # Entry window: US cash open (UTC 13:00 or 14:00)
        # Allows 1 trade per calendar day
        if current_hour in (13, 14) and self.last_trade_day != current_day:
            if self.asia_open_price is not None and self.asia_close_price is not None:
                asia_return_pct = (self.asia_close_price - self.asia_open_price) / self.asia_open_price

                # Require positive Asia drift of at least +0.20%
                if asia_return_pct > 0.0020:
                    atr = self._atr(highs, lows, closes, 14)
                    sma20 = self._sma(closes, 20)

                    # Dynamic structural filter: Ensure price didn't collapse below SMA20 during London session
                    if sma20 is not None and ctx.bar.close >= sma20:
                        # Macro sentiment sanity check (do not enter during full panic)
                        fg_index = ctx.features.get("fear_greed_index", 50)
                        if fg_index > 20:
                            # Volatility-based structural stop calculation
                            if atr is not None and ctx.bar.close > 0:
                                atr_bps = (atr / ctx.bar.close) * 10000.0
                                dynamic_sl = min(max(atr_bps * 1.5, 120.0), 280.0)
                                dynamic_tp = dynamic_sl * 2.0
                            else:
                                dynamic_sl = 200.0
                                dynamic_tp = 400.0

                            self.last_trade_day = current_day

                            # Scale confidence with the strength of the drift
                            conf = min(0.55 + (asia_return_pct * 10), 0.90)

                            return ctx.signal(
                                "long",
                                confidence=conf,
                                stop_loss_bps=dynamic_sl,
                                take_profit_bps=dynamic_tp,
                                horizon_seconds=28800,
                                metadata={
                                    "reason": "asia_drift_positive_us_continuation",
                                    "asia_return_pct": round(asia_return_pct, 4),
                                    "dynamic_sl_bps": round(dynamic_sl, 1),
                                    "dynamic_tp_bps": round(dynamic_tp, 1),
                                    "hour": current_hour,
                                    "fg_index": fg_index,
                                }
                            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index