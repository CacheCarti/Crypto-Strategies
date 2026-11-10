from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 43200,  # ~12 hours swing target
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.bars_in_position = 0

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
        self.bars_in_position = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Position tracking and timed/structural exit
        if ctx.has_position():
            self.bars_in_position += 1
            # Timed expiration to avoid getting chopped by giveback
            if self.bars_in_position >= 16:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "timed_squeeze_exhaustion",
                        "bars_held": self.bars_in_position,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Mandatory cooldown
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid crisis/meltdown chop
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        curr_close = closes[-1]
        past_close = closes[-self.lookback]
        pct_return_24 = (curr_close - past_close) / past_close

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or curr_close == 0:
            return None
        atr_bps = (atr / curr_close) * 10000.0

        # Dynamic volatility stops (structural change from failed fixed parameters)
        sl_bps = max(200.0, min(450.0, atr_bps * 1.8))
        tp_bps = sl_bps * 2.0

        # Bullish Squeeze: Price up over 24 bars (>1.5%) while funding stays negative (crowded shorts)
        if pct_return_24 > 0.015 and funding < -0.00005:
            conf = min(0.9, 0.55 + abs(funding) * 2000.0 + (pct_return_24 * 5.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding_rate": funding,
                    "return_24b": round(pct_return_24, 4),
                    "atr_bps": round(atr_bps, 1),
                    "price": curr_close,
                }
            )

        # Bearish Squeeze: Price down over 24 bars (<-1.5%) while funding stays positive (crowded longs)
        if pct_return_24 < -0.015 and funding > 0.00005:
            conf = min(0.9, 0.55 + funding * 2000.0 + (abs(pct_return_24) * 5.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_liquidation_continuation",
                    "funding_rate": funding,
                    "return_24b": round(pct_return_24, 4),
                    "atr_bps": round(atr_bps, 1),
                    "price": curr_close,
                }
            )

        return None