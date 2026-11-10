from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BtcFundingSqueeze",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 50,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 16
        self.last_exit_bar = -100
        self.min_return_pct = 1.2
        self.funding_threshold = 0.00003  # 0.003% bias threshold

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
        closes = ctx.closes(self.lookback + 20)
        highs = ctx.highs(self.lookback + 20)
        lows = ctx.lows(self.lookback + 20)
        
        if len(closes) < self.lookback + 15:
            return None

        # Cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Feature extraction
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        current_close = ctx.bar.close

        # Volatility check & dynamic thresholds via ATR
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr == 0:
            return None
        
        atr_pct = (atr / current_close) * 100.0

        # Don't trade in crisis or meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "regime_risk_exit", "regime": market_regime})
            return None

        # 24-bar cumulative price return %
        price_24h_ago = closes[-self.lookback]
        ret_24h = ((current_close - price_24h_ago) / price_24h_ago) * 100.0

        # Breakout / Momentum confirmation: 5-bar momentum confirming immediate direction
        mom_5 = (current_close - closes[-5]) / closes[-5] * 100.0

        # Existing position management: profit protection or trend divergence dissipation
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position.get("entry_price", current_close)
            pos_ret_bps = ((current_close - entry_price) / entry_price) * 10000.0 if pos_dir == "long" else ((entry_price - current_close) / entry_price) * 10000.0
            
            # Trailing target reached or funding flipped symmetrically against squeeze thesis
            if pos_dir == "long" and (funding > 0.0002 or pos_ret_bps > 400.0):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "squeeze_exhausted_long", "funding": funding, "pnl_bps": pos_ret_bps})
            elif pos_dir == "short" and (funding < -0.0002 or pos_ret_bps > 400.0):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "squeeze_exhausted_short", "funding": funding, "pnl_bps": pos_ret_bps})
            return None

        # SQUEEZE LONG SETUP:
        # Price is up solidly over 24h, short-term momentum is positive, yet funding is firmly negative (trapped bears)
        long_divergence = (ret_24h > self.min_return_pct) and (funding < -self.funding_threshold) and (mom_5 > 0.3)
        # Structural check: must be closing near the highs of the last 24 bars (breakout strength)
        highest_24 = max(highs[-self.lookback:])
        at_highs = current_close >= (highest_24 - 0.5 * atr)

        if long_divergence and at_highs:
            # Scaled confidence based on depth of negative funding
            neg_bias = min(abs(funding) / 0.0003, 1.0)
            conf = min(0.65 + 0.30 * neg_bias, 0.95)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_continuation",
                    "funding": funding,
                    "ret_24h": ret_24h,
                    "mom_5": mom_5,
                    "atr_pct": atr_pct
                }
            )

        # SQUEEZE SHORT SETUP:
        # Price is down solidly over 24h, short-term momentum is negative, yet funding is persistently positive (trapped bulls)
        short_divergence = (ret_24h < -self.min_return_pct) and (funding > self.funding_threshold) and (mom_5 < -0.3)
        # Structural check: must be closing near the lows of the last 24 bars (breakdown strength)
        lowest_24 = min(lows[-self.lookback:])
        at_lows = current_close <= (lowest_24 + 0.5 * atr)

        if short_divergence and at_lows:
            pos_bias = min(funding / 0.0003, 1.0)
            conf = min(0.65 + 0.30 * pos_bias, 0.95)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_liquidation_squeeze",
                    "funding": funding,
                    "ret_24h": ret_24h,
                    "mom_5": mom_5,
                    "atr_pct": atr_pct
                }
            )

        return None