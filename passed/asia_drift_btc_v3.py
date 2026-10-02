from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SessionDriftContinuation(Strategy):
    METADATA = {
        "name": "BTC Asia Drift US Continuation",
        "domain": "btc_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 28800,  # ~8 hours hold
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.min_asia_drift = 0.0035  # +0.35% minimum drift during Asia session
        self.entry_hour = 13          # US session open window (13:00 UTC)
        self.exit_hour = 22           # Pre-Asia session close (22:00 UTC)
        
        # State tracking across bars
        self.current_day: Optional[int] = None
        self.asia_open_price: Optional[float] = None
        self.asia_close_price: Optional[float] = None
        self.traded_today: bool = False
        self.last_exit_bar: int = -50

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        ts = ctx.bar.timestamp
        day_key = ts.toordinal() if hasattr(ts, "toordinal") else (ts.year * 1000 + ts.timetuple().tm_yday)
        hour = ts.hour

        # Reset daily state on a new calendar day
        if self.current_day != day_key:
            self.current_day = day_key
            self.asia_open_price = None
            self.asia_close_price = None
            self.traded_today = False

        # Capture Asia session opening price (00:00 UTC)
        if hour == 0 and self.asia_open_price is None:
            self.asia_open_price = ctx.bar.open

        # Capture Asia session close / drift price (08:00 UTC)
        if hour == 8 and self.asia_close_price is None:
            self.asia_close_price = ctx.bar.close

        # Position exit: Close before the next Asia session starts (at exit_hour UTC)
        if ctx.has_position() and hour >= self.exit_hour:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "flat",
                confidence=0.75,
                metadata={
                    "reason": "pre_asia_session_exit",
                    "hour": hour,
                    "close": ctx.bar.close,
                    "bar_index": ctx.bar_index,
                },
            )

        # Cooldown guard: wait at least 3 bars after an exit
        if ctx.bar_index - self.last_exit_bar < 3:
            return None

        # Entry logic: US session open window
        if hour == self.entry_hour and not ctx.has_position() and not self.traded_today:
            # Fallback if 00:00 bar wasn't exact
            if self.asia_open_price is None and len(closes) >= 14:
                self.asia_open_price = closes[-14]
            if self.asia_close_price is None and len(closes) >= 6:
                self.asia_close_price = closes[-6]

            if self.asia_open_price and self.asia_close_price and self.asia_open_price > 0:
                asia_drift = (self.asia_close_price - self.asia_open_price) / self.asia_open_price

                # Check if Asia session showed meaningful upward continuation drift
                if asia_drift >= self.min_asia_drift:
                    ema20 = self._ema(closes, 20)
                    ema50 = self._ema(closes, 50)
                    rsi = self._rsi(closes, 14)

                    # Trend and momentum filter: price above EMA20 and RSI healthy (not extreme overbought)
                    if ema20 is not None and ema50 is not None and rsi is not None:
                        if ctx.bar.close > ema50 and 42.0 <= rsi <= 74.0:
                            self.traded_today = True
                            confidence = min(0.90, max(0.60, 0.60 + (asia_drift * 15.0)))
                            
                            return ctx.signal(
                                "long",
                                confidence=confidence,
                                stop_loss_bps=self.METADATA["declared_sl_bps"],
                                take_profit_bps=self.METADATA["declared_tp_bps"],
                                horizon_seconds=self.METADATA["declared_hold_seconds"],
                                metadata={
                                    "reason": "asia_bullish_drift_us_continuation",
                                    "asia_drift_bps": round(asia_drift * 10000.0, 1),
                                    "rsi": round(rsi, 2),
                                    "ema20": round(ema20, 2),
                                    "ema50": round(ema50, 2),
                                    "hour": hour,
                                },
                            )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index