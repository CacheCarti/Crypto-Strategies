from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class USSessionMomentumBTC(Strategy):
    METADATA = {
        "name": "USSessionMomentumBTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # up to 6 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.atr_period = 14
        self.last_traded_date = None
        self.last_exit_bar = -999
        self.cooldown_bars = 4

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
        closes = ctx.closes(self.lookback + self.atr_period + 5)
        if len(closes) < self.lookback + self.atr_period:
            return None

        current_dt = ctx.bar.timestamp
        hour = current_dt.hour
        today_str = current_dt.strftime("%Y-%m-%d")

        # 1. Mandatory timed exit before UTC 21:00
        if ctx.has_position():
            if hour >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_pre_21_utc", "hour": hour, "price": ctx.bar.close}
                )
            return None

        # 2. Strict session window & daily budget
        # Enter primarily at US open / early session (UTC 13 to 15)
        if hour < 13 or hour > 15:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # One trade per calendar day
        if self.last_traded_date == today_str:
            return None

        # 3. Market Regime Filter (avoid meltdown / extreme crisis)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 4. Volatility-Adaptive Risk & Trend Calculation
        highs = ctx.highs(self.lookback + self.atr_period + 5)
        lows = ctx.lows(self.lookback + self.atr_period + 5)
        atr = self._atr(highs, lows, closes, period=self.atr_period)
        if atr is None or atr <= 0:
            return None

        price = ctx.bar.close
        atr_bps = (atr / price) * 10000.0

        # Calculate momentum over past 12 bars (Asian/European session trend)
        c_now = closes[-1]
        c_prev = closes[-self.lookback]
        mom_return_bps = ((c_now - c_prev) / c_prev) * 10000.0

        # Require meaningful pre-session directional impulse (> 0.75 * ATR)
        threshold_bps = max(40.0, atr_bps * 0.75)

        # Dynamic structure stops based on recent volatility
        sl_bps = max(140.0, min(300.0, atr_bps * 1.6))
        tp_bps = max(240.0, min(500.0, atr_bps * 2.8))

        if mom_return_bps > threshold_bps:
            self.last_traded_date = today_str
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "us_open_bullish_continuation",
                    "mom_bps": mom_return_bps,
                    "atr_bps": atr_bps,
                    "hour": hour,
                    "price": price
                }
            )

        if mom_return_bps < -threshold_bps:
            self.last_traded_date = today_str
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "us_open_bearish_continuation",
                    "mom_bps": mom_return_bps,
                    "atr_bps": atr_bps,
                    "hour": hour,
                    "price": price
                }
            )

        return None