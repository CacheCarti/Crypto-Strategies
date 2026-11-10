from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class UsSessionMomentumBtc(Strategy):
    METADATA = {
        "name": "UsSessionMomentumBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours max
        "warmup_bars": 30,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.last_trade_day = -1
        self.bars_in_position = 0
        self.cooldown_until = 0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.cooldown_until = ctx.bar_index + 4
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_time = ctx.bar.timestamp
        hour = current_time.hour
        day_key = current_time.toordinal()

        # Hard session close: flatten before UTC 21 or after session ends
        if ctx.has_position():
            self.bars_in_position += 1
            if hour >= 21 or hour < 13:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "session_close_time_exit", "hour": hour, "bars_held": self.bars_in_position}
                )

        # Skip entry if in cooldown or already traded today
        if ctx.bar_index < self.cooldown_until:
            return None
        if ctx.has_position():
            return None
        if self.last_trade_day == day_key:
            return None

        # Entry window: strictly at US session open hours (UTC 13 or 14)
        if hour not in (13, 14):
            return None

        # Volatility regime gate: don't enter in meltdown / extreme crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # Measure 12-bar pre-session momentum
        p_now = closes[-1]
        p_12 = closes[-13]
        pre_return_bps = ((p_now - p_12) / p_12) * 10000.0

        atr = self._atr(highs, lows, closes, 14)
        if atr is None or atr == 0:
            return None

        atr_bps = (atr / p_now) * 10000.0
        # Dynamic volatility stop: 1.5x ATR capped between 120 and 280 bps
        dyn_sl = max(120.0, min(280.0, atr_bps * 1.5))
        dyn_tp = dyn_sl * 1.8

        # Directional threshold: at least 70 bps move over last 12 bars (0.7%)
        min_momentum_bps = 70.0
        btc_24h_ret = ctx.features.get("btc_return_pct", 0.0)

        # Long continuation
        if pre_return_bps > min_momentum_bps and btc_24h_ret > -2.0:
            self.last_trade_day = day_key
            self.bars_in_position = 0
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "us_session_bull_continuation",
                    "pre_return_bps": round(pre_return_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "btc_24h_ret": round(btc_24h_ret, 2),
                    "hour": hour
                }
            )

        # Short continuation
        if pre_return_bps < -min_momentum_bps and btc_24h_ret < 2.0:
            self.last_trade_day = day_key
            self.bars_in_position = 0
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                metadata={
                    "reason": "us_session_bear_continuation",
                    "pre_return_bps": round(pre_return_bps, 2),
                    "atr_bps": round(atr_bps, 2),
                    "btc_24h_ret": round(btc_24h_ret, 2),
                    "hour": hour
                }
            )

        return None