from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours intended hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Cooldown guard after position exits
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check existing position management
        if ctx.has_position():
            # Time-based max horizon safeguard (~18 bars = 18h)
            if ctx.bar_index - self.entry_bar >= 18:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_horizon_reached",
                    "bars_held": ctx.bar_index - self.entry_bar,
                    "close": ctx.bar.close
                })
            return None

        # Feature: BTC Perp Funding Rate
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Divergence metrics: 24-bar price return
        price_now = closes[-1]
        price_base = closes[-self.lookback]
        ret_24 = (price_now - price_base) / price_base

        # Normalized volatility filter using ATR
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr_val = self._atr(highs, lows, closes, period=14)
        if atr_val is None or atr_val <= 0:
            return None
        
        atr_pct = atr_val / price_now

        # Avoid extreme crisis conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # High-conviction Divergence Conditions:
        # Long Squeeze: Price is pushing up solidly (>1.2%), but funding is deeply negative (shorts are paying to hold into a rally)
        bullish_squeeze = (ret_24 > 0.012) and (funding_rate < -0.00008)

        # Short Squeeze: Price is breaking down (<-1.2%), but funding remains stubbornly positive (longs are trapped and paying)
        bearish_squeeze = (ret_24 < -0.012) and (funding_rate > 0.00015)

        # Dynamic SL based on ATR (clamped between 150 and 320 bps)
        sl_bps = max(150.0, min(320.0, (atr_pct * 1.5) * 10000.0))
        tp_bps = sl_bps * 2.0  # 1:2 risk-reward target

        if bullish_squeeze:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.6 + abs(funding_rate) * 2000.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=28800,
                metadata={
                    "reason": "short_funding_squeeze_long_continuation",
                    "ret_24": round(ret_24, 4),
                    "funding_rate": funding_rate,
                    "atr_pct": round(atr_pct, 4),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1)
                }
            )

        if bearish_squeeze:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.6 + abs(funding_rate) * 2000.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=28800,
                metadata={
                    "reason": "long_funding_squeeze_short_continuation",
                    "ret_24": round(ret_24, 4),
                    "funding_rate": funding_rate,
                    "atr_pct": round(atr_pct, 4),
                    "sl_bps": round(sl_bps, 1),
                    "tp_bps": round(tp_bps, 1)
                }
            )

        return None