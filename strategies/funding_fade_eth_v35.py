from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingContrarianSqueeze(Strategy):
    METADATA = {
        "name": "EthFundingContrarianSqueeze",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_history = []
        self.last_exit_bar = -999
        self.cooldown_bars = 8
        self.funding_persist_bars = 3
        self.extreme_long_thresh = -0.00015
        self.extreme_short_thresh = 0.00030
        self.norm_long_exit = -0.00003
        self.norm_short_exit = 0.00008

    def _sma(self, values, period: int):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period: int = 14):
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

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        self.funding_history.append(funding)
        if len(self.funding_history) > 50:
            self.funding_history.pop(0)

        closes = ctx.closes(40)
        if closes is None or len(closes) < 35:
            return None

        current_price = ctx.bar.close
        sma_filter = self._sma(closes, 30)
        rsi_val = self._rsi(closes, 14)
        if sma_filter is None or rsi_val is None:
            return None

        # Manage open position exits via funding normalization
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and funding >= self.norm_long_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_long_exit", "funding": funding, "rsi": rsi_val}
                )
            elif pos_dir == "short" and funding <= self.norm_short_exit:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized_short_exit", "funding": funding, "rsi": rsi_val}
                )
            return None

        # Strict cooldown to keep trade counts in optimal friction-resistant range
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Skip trades during extreme market-wide meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        if len(self.funding_history) < self.funding_persist_bars:
            return None

        recent_funding = self.funding_history[-self.funding_persist_bars:]
        long_persist = all(f <= self.extreme_long_thresh for f in recent_funding)
        short_persist = all(f >= self.extreme_short_thresh for f in recent_funding)

        # Volatility state & light anti-trend exhaustion confirmation
        dist_sma_pct = (current_price - sma_filter) / sma_filter

        # Long Squeeze: Crowd heavily short, but price is not crashing uncontrollably (RSI recovering > 30 and > 20)
        if long_persist and rsi_val > 32.0 and dist_sma_pct > -0.05:
            confidence = min(1.0, 0.65 + abs(funding) * 1000)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "funding_short_crowd_squeeze",
                    "funding": funding,
                    "rsi": rsi_val,
                    "dist_sma_pct": dist_sma_pct,
                    "price": current_price
                }
            )

        # Short Squeeze/Fade: Crowd euphoric long, but upside momentum is exhausted (RSI < 68 and not in hyper-run)
        if short_persist and rsi_val < 68.0 and dist_sma_pct < 0.05:
            confidence = min(1.0, 0.65 + abs(funding) * 1000)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=350.0,
                take_profit_bps=500.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "funding_long_crowd_exhaustion",
                    "funding": funding,
                    "rsi": rsi_val,
                    "dist_sma_pct": dist_sma_pct,
                    "price": current_price
                }
            )

        return None