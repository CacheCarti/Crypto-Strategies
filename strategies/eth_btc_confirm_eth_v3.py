from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthBtcLeaderConfirm(Strategy):
    METADATA = {
        "name": "EthBtcLeaderConfirm",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 3600 * 4,  # Aim for a 4-hour hold
        "warmup_bars": 60,  # Need enough bars for EMA and momentum calculations
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.momentum_period = 5
        self.btc_volatility_period = 20
        self.min_btc_return_pct = 0.5  # BTC must move at least this much
        self.max_crisis_score = 0.3 # Don't trade if BTC crisis score is too high
        self.cooldown_bars = 5 # Cooldown period after a trade

        self.last_signal_bar_index = -self.cooldown_bars - 1 # Initialize to allow immediate trade

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _momentum(self, closes, period):
        if len(closes) < period + 1:
            return None
        return closes[-1] - closes[-period-1]

    def _stddev(self, values, period):
        if len(values) < period:
            return None
        slice_v = values[-period:]
        mean = sum(slice_v) / period
        variance = sum((x - mean) ** 2 for x in slice_v) / period
        return math.sqrt(variance)

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + 1)
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.btc_volatility_period)]

        if len(closes) < self.METADATA["warmup_bars"] or len(btc_returns) < self.btc_volatility_period:
            return None

        # Cooldown check
        if ctx.bar_index <= self.last_signal_bar_index + self.cooldown_bars:
            return None

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        eth_momentum = self._momentum(closes, self.momentum_period)

        if fast_ema is None or slow_ema is None or eth_momentum is None:
            return None

        # BTC volatility filter - using stddev of BTC returns as a proxy for crisis spike
        btc_volatility = self._stddev(btc_returns, self.btc_volatility_period)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Entry conditions
        signal_direction = None
        reason = ""
        confidence = 0.0

        # Long entry: ETH fast EMA crosses above slow EMA, ETH momentum is positive, BTC return agrees, and BTC is calm
        if fast_ema > slow_ema and closes[-2] <= slow_ema and eth_momentum > 0: # Fast EMA just crossed above slow EMA
            if ctx.features.get("btc_return_pct", 0.0) > self.min_btc_return_pct: # BTC is also moving up
                if btc_volatility is not None and crisis_score < self.max_crisis_score: # BTC is not too volatile/crisis
                    signal_direction = "long"
                    reason = "eth_ema_bull_cross_btc_confirm_calm"
                    confidence = 0.75 # High confidence
                else:
                    reason = "btc_volatile_or_crisis_prevented_long"

        # Short entry: ETH fast EMA crosses below slow EMA, ETH momentum is negative, BTC return agrees, and BTC is calm
        elif fast_ema < slow_ema and closes[-2] >= slow_ema and eth_momentum < 0: # Fast EMA just crossed below slow EMA
            if ctx.features.get("btc_return_pct", 0.0) < -self.min_btc_return_pct: # BTC is also moving down
                if btc_volatility is not None and crisis_score < self.max_crisis_score: # BTC is not too volatile/crisis
                    signal_direction = "short"
                    reason = "eth_ema_bear_cross_btc_confirm_calm"
                    confidence = 0.75 # High confidence
                else:
                    reason = "btc_volatile_or_crisis_prevented_short"

        # Exit conditions (momentum exhaustion or BTC confirmation loss)
        if ctx.has_position():
            if ctx.position_direction() == "long":
                if eth_momentum < 0 or ctx.features.get("btc_return_pct", 0.0) < 0: # ETH momentum turns negative or BTC turns negative
                    signal_direction = "flat"
                    reason = "long_exit_eth_momentum_loss_or_btc_reversal"
                    confidence = 0.6
            elif ctx.position_direction() == "short":
                if eth_momentum > 0 or ctx.features.get("btc_return_pct", 0.0) > 0: # ETH momentum turns positive or BTC turns positive
                    signal_direction = "flat"
                    reason = "short_exit_eth_momentum_loss_or_btc_reversal"
                    confidence = 0.6

        if signal_direction:
            self.last_signal_bar_index = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "eth_fast_ema": fast_ema,
                    "eth_slow_ema": slow_ema,
                    "eth_momentum": eth_momentum,
                    "btc_return_pct": ctx.features.get("btc_return_pct", 0.0),
                    "btc_volatility_stddev": btc_volatility,
                    "crisis_score": crisis_score,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for this strategy currently, just logging
        pass