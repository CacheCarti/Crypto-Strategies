from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeTrap(Strategy):
    METADATA = {
        "name": "FundingSqueezeTrap",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 8
        self.last_exit_bar = -100
        self.min_return_bps = 120.0
        self.funding_threshold = 0.00005

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
        closes = ctx.closes(self.lookback + 5)
        highs = ctx.highs(self.lookback + 5)
        lows = ctx.lows(self.lookback + 5)

        if len(closes) < self.lookback + 1:
            return None

        # Crisis filter: preserve capital during catastrophic regime
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.6:
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={"reason": "crisis_exit", "crisis_score": crisis_score}
                )
            return None

        # Position management
        if ctx.has_position():
            pos = ctx.position
            entry_price = pos["entry_price"]
            direction = pos["direction"]
            current_price = ctx.bar.close

            # Dynamic trailing/structural exit if divergence dissipates
            funding = ctx.features.get("funding_rate_btcusdt", 0.0)
            if direction == "long" and funding > self.funding_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "long_squeeze_relieved", "funding": funding, "price": current_price}
                )
            elif direction == "short" and funding < -self.funding_threshold:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "short_trap_relieved", "funding": funding, "price": current_price}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Event boundary: inspect price velocity over lookback
        price_now = closes[-1]
        price_prev = closes[-self.lookback]
        ret_bps = ((price_now - price_prev) / price_prev) * 10000.0

        # Donchian channel breakout boundary over lookback window (excluding current bar)
        recent_highs = highs[-self.lookback - 1 : -1]
        recent_lows = lows[-self.lookback - 1 : -1]
        highest_prev = max(recent_highs)
        lowest_prev = min(recent_lows)

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        atr_val = self._atr(highs, lows, closes, 14)
        if atr_val is None or atr_val <= 0:
            return None

        # LONG SQUEEZE TRIGGER:
        # Price makes fresh high breakout + significant positive trajectory, but funding remains persistently negative
        if price_now > highest_prev and ret_bps >= self.min_return_bps and funding < -0.00002:
            conf = min(0.9, 0.55 + min(abs(funding) * 5000.0, 0.25) + min(ret_bps / 1000.0, 0.1))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_funding_squeeze_breakout",
                    "ret_bps_24h": round(ret_bps, 1),
                    "funding": funding,
                    "breakout_level": round(highest_prev, 2),
                    "price": round(price_now, 2),
                }
            )

        # SHORT TRAP TRIGGER:
        # Price breaks recent low + downward trajectory, but market participants persistently pay positive funding
        if price_now < lowest_prev and ret_bps <= -self.min_return_bps and funding > 0.00004:
            conf = min(0.9, 0.55 + min(funding * 4000.0, 0.25) + min(abs(ret_bps) / 1000.0, 0.1))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_crowded_liquidation_breakdown",
                    "ret_bps_24h": round(ret_bps, 1),
                    "funding": funding,
                    "breakdown_level": round(lowest_prev, 2),
                    "price": round(price_now, 2),
                }
            )

        return None