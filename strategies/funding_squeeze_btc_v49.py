from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingSqueezeDivergence(Strategy):
    METADATA = {
        "name": "FundingSqueezeDivergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 40,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.cooldown_bars = 10
        self.last_trade_bar = -50
        self.funding_history = []
        self.max_hist_len = 120

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        if len(closes) < self.lookback + 1:
            return None

        # Track recent funding rates to evaluate relative distribution instead of arbitrary magic numbers
        current_funding = ctx.features.get("funding_rate_btcusdt", 0.0)
        self.funding_history.append(current_funding)
        if len(self.funding_history) > self.max_hist_len:
            self.funding_history.pop(0)

        # Skip during cooldown or if position is already managed
        if ctx.has_position():
            return None

        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Avoid extreme system meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.7:
            return None

        # Measure 24-bar price return
        price_now = closes[-1]
        price_past = closes[-self.lookback]
        if price_past <= 0:
            return None
        price_ret = (price_now - price_past) / price_past

        # Calculate relative positioning of current funding vs rolling window
        sorted_funding = sorted(self.funding_history)
        n = len(sorted_funding)
        rank = sum(1 for f in sorted_funding if f < current_funding)
        funding_percentile = rank / max(1, n)

        # Baseline threshold using ATR to avoid entering on flat noise
        highs = ctx.highs(15)
        lows = ctx.lows(15)
        recent_closes = ctx.closes(15)
        atr = self._atr(highs, lows, recent_closes, period=14)
        if atr is None or atr <= 0:
            return None

        min_move_pct = (atr * 1.5) / price_now

        # Divergence Setup 1: Short Squeeze
        # Price is decisively moving UP over 24 bars, but funding rate is compressed in lower quartile (shorts trapped/crowded)
        if price_ret > min_move_pct and funding_percentile <= 0.25 and current_funding <= 0.00005:
            conf = min(0.9, 0.55 + abs(price_ret) * 5.0)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=420.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "funding_short_squeeze_continuation",
                    "price_ret_24": round(price_ret, 4),
                    "funding": current_funding,
                    "funding_pctl": round(funding_percentile, 2),
                    "price": price_now,
                },
            )

        # Divergence Setup 2: Long Flush / Squeeze Down
        # Price is decisively falling over 24 bars, but funding rate remains elevated in top quartile (bulls holding underwater)
        if price_ret < -min_move_pct and funding_percentile >= 0.75 and current_funding >= 0.0:
            conf = min(0.9, 0.55 + abs(price_ret) * 5.0)
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=220.0,
                take_profit_bps=420.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "funding_long_trapped_flush",
                    "price_ret_24": round(price_ret, 4),
                    "funding": current_funding,
                    "funding_pctl": round(funding_percentile, 2),
                    "price": price_now,
                },
            )

        return None