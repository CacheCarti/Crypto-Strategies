from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # Target 4-hour hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_fast = 12
        self.ema_period_slow = 26
        self.cooldown_bars = 5 # Wait 5 bars after a trade before new entry
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        # Get BTC and ETH returns
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Calculate EMAs for ETH
        ema_fast = self._ema(closes, self.ema_period_fast)
        ema_slow = self._ema(closes, self.ema_period_slow)

        if ema_fast is None or ema_slow is None:
            return None

        # Entry conditions
        # Causal Driver: BTC Return % (recent, not stale)
        # We need to make sure btc_return_pct is "fresh" in the sense that it's actively driving price, not just a historical value
        # For simplicity, we'll check if it's above a certain threshold, implying recent movement.
        btc_strong_positive = btc_return_pct > 0.5 # BTC is up > 0.5%
        btc_strong_negative = btc_return_pct < -0.5 # BTC is down < -0.5%

        # Confirmation: ETH Trend (EMA cross)
        eth_bullish_confirmation = ema_fast > ema_slow and closes[-1] > ema_fast
        eth_bearish_confirmation = ema_fast < ema_slow and closes[-1] < ema_fast

        # Exit conditions
        # Relationship normalizes: EMA cross back or BTC return reverses
        # Time stop: declared_hold_seconds will handle this via engine

        if not ctx.has_position():
            # Long signal: BTC strong positive and ETH bullish confirmation
            if btc_strong_positive and eth_bullish_confirmation:
                # Check for an actual crossover, not just being above/below
                prev_ema_fast = self._ema(ctx.closes(self.warmup_bars - 1), self.ema_period_fast)
                prev_ema_slow = self._ema(ctx.closes(self.warmup_bars - 1), self.ema_period_slow)
                if prev_ema_fast is not None and prev_ema_slow is not None and prev_ema_fast <= prev_ema_slow: # Was below or equal, now above
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        metadata={
                            "reason": "btc_leader_eth_trend_confirmation_long",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_price": closes[-1]
                        }
                    )

            # Short signal: BTC strong negative and ETH bearish confirmation
            elif btc_strong_negative and eth_bearish_confirmation:
                # Check for an actual crossover, not just being above/below
                prev_ema_fast = self._ema(ctx.closes(self.warmup_bars - 1), self.ema_period_fast)
                prev_ema_slow = self._ema(ctx.closes(self.warmup_bars - 1), self.ema_period_slow)
                if prev_ema_fast is not None and prev_ema_slow is not None and prev_ema_fast >= prev_ema_slow: # Was above or equal, now below
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        metadata={
                            "reason": "btc_leader_eth_trend_confirmation_short",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_price": closes[-1]
                        }
                    )
        else: # If a position is open
            current_direction = ctx.position_direction()

            # Exit if BTC leader disagrees with current position, or ETH trend reverses
            if current_direction == "long":
                # BTC turns negative OR ETH trend weakens/reverses
                if btc_strong_negative or (ema_fast < ema_slow and closes[-1] < ema_fast):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "long_exit_btc_disagrees_or_eth_trend_reverses",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_price": closes[-1]
                        }
                    )
            elif current_direction == "short":
                # BTC turns positive OR ETH trend weakens/reverses
                if btc_strong_positive or (ema_fast > ema_slow and closes[-1] > ema_fast):
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "short_exit_btc_disagrees_or_eth_trend_reverses",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "ema_fast": ema_fast,
                            "ema_slow": ema_slow,
                            "current_price": closes[-1]
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive sizing for this strategy, just logging.
        pass