from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueeze(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Breakout",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 700.0,
        "declared_hold_seconds": 36000,
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.funding_threshold = 0.00005
        self.max_hold_bars = 36

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
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Manage existing position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_hold_timeout", "bars_held": bars_held}
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid extreme breakdown / meltdown environments
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        current_close = closes[-1]
        prev_close = closes[-2]

        atr = self._atr(highs, lows, closes, period=14)
        if atr is None or atr <= 0:
            return None

        # 24-bar window channels excluding current candle
        window_high = max(highs[-self.lookback - 1:-1])
        window_low = min(lows[-self.lookback - 1:-1])
        base_price = closes[-self.lookback - 1]
        price_return_pct = (current_close - base_price) / base_price

        # Event boundary: Stricter breakout of 24-bar high/low combined with persistent positioning divergence
        # Long squeeze: Price making a fresh breakout high and up over 24h, yet funding is persistent negative
        if current_close > window_high and prev_close <= window_high:
            if price_return_pct > 0.015 and funding < -self.funding_threshold:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + abs(funding) * 2000.0)
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "short_squeeze_breakout",
                        "funding_rate": funding,
                        "return_pct": round(price_return_pct, 4),
                        "atr": round(atr, 2),
                        "window_high": round(window_high, 2),
                        "current_close": round(current_close, 2)
                    }
                )

        # Short squeeze: Price breaking 24-bar low and down over 24h, yet funding is persistent positive
        if current_close < window_low and prev_close >= window_low:
            if price_return_pct < -0.015 and funding > self.funding_threshold:
                self.entry_bar = ctx.bar_index
                conf = min(0.9, 0.5 + abs(funding) * 2000.0)
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "long_liquidation_breakdown",
                        "funding_rate": funding,
                        "return_pct": round(price_return_pct, 4),
                        "atr": round(atr, 2),
                        "window_low": round(window_low, 2),
                        "current_close": round(current_close, 2)
                    }
                )

        return None