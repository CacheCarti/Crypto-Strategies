from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueeze(Strategy):
    METADATA = {
        "name": "FundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 86400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.max_hold_bars = 36
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.prev_setup = 0  # 1 for bull squeeze, -1 for bear squeeze

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
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Manage existing position exits
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            # Timed exit to structurally prevent trailing allocation stops from bleeding out
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_exit", "bars_held": bars_held}
                )
            return None

        # Cooldown guard after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Feature inputs
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        
        # Price dynamics over 24 bars
        current_close = closes[-1]
        prior_close = closes[-self.lookback]
        price_return = (current_close - prior_close) / prior_close

        # Volatility stop scaling via ATR
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, ctx.closes(20), period=14)
        if atr is None or atr <= 0:
            return None

        atr_bps = (atr / current_close) * 10000.0
        # Dynamic stop: 2.0x ATR, bounded between 150 and 320 bps
        sl_bps = max(150.0, min(320.0, atr_bps * 2.0))
        tp_bps = sl_bps * 2.0

        # Market regime filter: avoid entering during extreme crisis panics
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Trapped shorts setup: Price rising strongly over 24h while perpetual funding is negative
        # Transition trigger: must not have triggered setup on previous bar
        bull_squeeze = (price_return > 0.015) and (funding_rate < -0.00005)
        # Trapped longs setup: Price falling strongly over 24h while perpetual funding is positive
        bear_squeeze = (price_return < -0.015) and (funding_rate > 0.0001)

        current_setup = 1 if bull_squeeze else (-1 if bear_squeeze else 0)

        # Ensure we only fire on transition into the setup
        if bull_squeeze and self.prev_setup != 1:
            self.prev_setup = 1
            self.entry_bar = ctx.bar_index
            conf = min(0.9, max(0.6, 0.6 + abs(price_return) * 5.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "funding_squeeze_shorts_trapped",
                    "price_return_24b": round(price_return, 4),
                    "funding_rate": funding_rate,
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1)
                }
            )

        if bear_squeeze and self.prev_setup != -1:
            self.prev_setup = -1
            self.entry_bar = ctx.bar_index
            conf = min(0.9, max(0.6, 0.6 + abs(price_return) * 5.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                metadata={
                    "reason": "funding_squeeze_longs_trapped",
                    "price_return_24b": round(price_return, 4),
                    "funding_rate": funding_rate,
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1)
                }
            )

        self.prev_setup = current_setup
        return None