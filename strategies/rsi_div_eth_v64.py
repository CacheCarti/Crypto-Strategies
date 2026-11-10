from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 45,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 30
        self.cooldown_bars = 8
        self.last_trade_bar = -999

    def _calc_rsi(self, closes, period=14):
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
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period + 5:
            return None

        # Build RSI series over recent window
        rsi_series = []
        for end_idx in range(len(closes) - self.lookback, len(closes) + 1):
            sub_closes = closes[:end_idx]
            val = self._calc_rsi(sub_closes, self.rsi_period)
            rsi_series.append(val)

        curr_rsi = rsi_series[-1]
        if curr_rsi is None:
            return None

        # Manage existing position exits
        if ctx.has_position():
            direction = ctx.position_direction()
            # Mean reversion normalization exit
            if direction == "long" and curr_rsi >= 55.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reverted_bull_exit", "rsi": curr_rsi})
            elif direction == "short" and curr_rsi <= 45.0:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reverted_bear_exit", "rsi": curr_rsi})
            return None

        # Cooldown enforcement
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Macro/volatility regime filter: avoid trade entries during meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        # Cross-asset momentum confirmation (ETH vs BTC alignment avoids false divergences)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Identify local pivots over the lookback window
        window_closes = closes[-self.lookback:]
        # Find pivot low & pivot high in the lookback window
        min_p = min(window_closes)
        max_p = max(window_closes)
        min_idx = window_closes.index(min_p)
        max_idx = window_closes.index(max_p)

        curr_price = closes[-1]
        prior_rsi_at_min = rsi_series[min_idx]
        prior_rsi_at_max = rsi_series[max_idx]

        # Regular Bullish Divergence:
        # Price at or near recent low, but current RSI significantly higher than RSI at that prior low
        # Filter: RSI must be in oversold-rebound territory (< 42) and BTC return not heavily negative (> -2.5%)
        if (
            min_idx < (self.lookback - 4)  # Prior pivot formed at least 4 bars ago
            and curr_price <= min_p * 1.006  # Price makes equivalent or lower low
            and prior_rsi_at_min is not None
            and curr_rsi > prior_rsi_at_min + 4.0  # Clear higher low on RSI
            and curr_rsi < 42.0
            and btc_ret > -2.5
        ):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.78,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bullish_rsi_div_confirmed",
                    "curr_rsi": round(curr_rsi, 2),
                    "prior_rsi": round(prior_rsi_at_min, 2),
                    "price_min": round(min_p, 2),
                    "curr_price": round(curr_price, 2),
                    "btc_ret": round(btc_ret, 2),
                },
            )

        # Regular Bearish Divergence:
        # Price at or near recent high, but current RSI significantly lower than RSI at that prior high
        # Filter: RSI must be in overbought-turn territory (> 58) and BTC return not screaming higher (< 2.5%)
        if (
            max_idx < (self.lookback - 4)
            and curr_price >= max_p * 0.994  # Price makes equivalent or higher high
            and prior_rsi_at_max is not None
            and curr_rsi < prior_rsi_at_max - 4.0  # Clear lower high on RSI
            and curr_rsi > 58.0
            and btc_ret < 2.5
        ):
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.78,
                stop_loss_bps=250.0,
                take_profit_bps=400.0,
                horizon_seconds=18000,
                metadata={
                    "reason": "bearish_rsi_div_confirmed",
                    "curr_rsi": round(curr_rsi, 2),
                    "prior_rsi": round(prior_rsi_at_max, 2),
                    "price_max": round(max_p, 2),
                    "curr_price": round(curr_price, 2),
                    "btc_ret": round(btc_ret, 2),
                },
            )

        return None