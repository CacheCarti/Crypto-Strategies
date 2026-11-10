from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 28800,  # ~8 hours typical hold
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.bars_in_trade = 0

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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 5)
        if len(closes) < self.lookback + 1:
            return None

        # Manage open position hold time
        if ctx.has_position():
            self.bars_in_trade += 1
            # Time-based exhaustion exit: if squeeze doesn't materialize within 16 bars
            if self.bars_in_trade >= 16:
                return ctx.signal("flat", confidence=0.5, metadata={"reason": "max_bars_reached", "bars_held": self.bars_in_trade})
            return None

        # Mandatory cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Avoid extreme crisis / meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        current_close = closes[-1]
        past_close = closes[-self.lookback - 1]
        price_return_pct = (current_close - past_close) / past_close

        # Volatility check via ATR to ensure meaningful price movement
        highs = ctx.highs(20)
        lows = ctx.lows(20)
        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # Normalized move: price return relative to volatility
        normalized_move = (current_close - past_close) / atr

        # Funding rate positioning
        funding = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Divergence Conditions:
        # Long squeeze: Price pushed higher (> 1.5 ATRs over 24h) while aggregate funding remains negative (shorts trapped).
        short_squeeze_long = (normalized_move > 1.5) and (funding < -0.00005)

        # Long unwind / short breakdown: Price pushed lower (< -1.5 ATRs over 24h) while funding remains positive (longs trapped).
        long_squeeze_short = (normalized_move < -1.5) and (funding > 0.00005)

        if short_squeeze_long:
            # Scale confidence with the strength of the divergence
            confidence = min(0.9, max(0.55, 0.55 + abs(funding) * 2000.0))
            self.bars_in_trade = 0
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_squeeze_funding_divergence",
                    "funding_rate": funding,
                    "normalized_move": round(normalized_move, 2),
                    "price_return_pct": round(price_return_pct, 4),
                    "atr": round(atr, 2),
                },
            )

        if long_squeeze_short:
            confidence = min(0.9, max(0.55, 0.55 + abs(funding) * 2000.0))
            self.bars_in_trade = 0
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_liquidation_funding_divergence",
                    "funding_rate": funding,
                    "normalized_move": round(normalized_move, 2),
                    "price_return_pct": round(price_return_pct, 4),
                    "atr": round(atr, 2),
                },
            )

        return None