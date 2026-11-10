from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # 8 hours typical hold
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_trade_bar = -100

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 1)
        if len(closes) < self.lookback + 1:
            return None

        # Cooldown guard to avoid churn and friction bleed
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Read funding rate
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # 24-hour return percentage
        current_close = closes[-1]
        past_close = closes[0]
        ret_24h = (current_close - past_close) / past_close

        # Volatility check via ATR to scale threshold dynamically
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes[-20:], period=14)
        if atr is None or current_close == 0:
            return None

        norm_atr = (atr / current_close)

        # We require at least 1.5 ATR movement over 24h to constitute meaningful displacement
        min_move = max(norm_atr * 1.5, 0.01)

        # Skip trading during full blown crisis meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Long Squeeze: Price is up solidly over 24h while funding is negative (shorts crowded and trapped)
        if ret_24h > min_move and funding < -0.00005:
            if not ctx.has_position() or ctx.position_direction() == "short":
                self.last_trade_bar = ctx.bar_index
                conf = min(0.9, 0.6 + abs(funding) * 1500)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "short_squeeze_continuation",
                        "ret_24h": ret_24h,
                        "funding": funding,
                        "min_move": min_move,
                        "regime": regime
                    }
                )

        # Short Squeeze: Price is down solidly over 24h while funding is positive (longs crowded and trapped)
        if ret_24h < -min_move and funding > 0.0001:
            if not ctx.has_position() or ctx.position_direction() == "long":
                self.last_trade_bar = ctx.bar_index
                conf = min(0.9, 0.6 + funding * 1500)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "long_liquidation_continuation",
                        "ret_24h": ret_24h,
                        "funding": funding,
                        "min_move": min_move,
                        "regime": regime
                    }
                )

        # Early exit on momentum exhaustion / funding neutralization
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and funding >= 0.0002:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "funding_normalized_exit", "funding": funding}
                )
            elif pos_dir == "short" and funding <= -0.0001:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "funding_normalized_exit", "funding": funding}
                )

        return None