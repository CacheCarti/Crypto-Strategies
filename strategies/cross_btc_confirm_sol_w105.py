from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirmation(Strategy):
    METADATA = {
        "name": "Cross-Asset Trend Confirmation",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3, # Aim for a few hours
        "warmup_bars": 60, # Need enough for 2 * EMA_period + return_lookback
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.btc_return_lookback = 3 # bars for BTC return freshness
        self.sol_return_lookback = 2 # bars for SOL return freshness
        self.min_btc_return_confirm = 0.5 # % BTC must return
        self.min_sol_return_confirm = 0.2 # % SOL must return
        self.cooldown_bars = 5 # Wait this many bars after a trade
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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(self.slow_ema_period + 1)
        if len(closes) < self.slow_ema_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)

        if fast_ema is None or slow_ema is None:
            return None

        current_close = ctx.bar.close

        # Get BTC and SOL recent returns
        btc_returns_raw = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.btc_return_lookback)]
        sol_returns_raw = [ctx.features.get("sol_return_pct", 0.0) for _ in range(self.sol_return_lookback)]

        # BTC as the leader: look for recent positive returns
        btc_confirms_long = all(r > self.min_btc_return_confirm for r in btc_returns_raw)
        btc_confirms_short = all(r < -self.min_btc_return_confirm for r in btc_returns_raw)

        # SOL as the follower: look for recent positive returns
        sol_confirms_long = all(r > self.min_sol_return_confirm for r in sol_returns_raw)
        sol_confirms_short = all(r < -self.min_sol_return_confirm for r in sol_returns_raw)

        # BTC strongly disagrees with SOL's direction
        btc_disagrees_long = any(r < -self.min_btc_return_confirm for r in btc_returns_raw)
        btc_disagrees_short = any(r > self.min_btc_return_confirm for r in btc_returns_raw)


        # Entry logic
        if not ctx.has_position():
            # Long condition: SOL fast EMA above slow EMA, BTC confirms, SOL confirms, BTC doesn't disagree
            if (fast_ema > slow_ema and closes[-2] <= fast_ema and current_close > fast_ema and # Price crossed above fast EMA, fast EMA above slow EMA
                btc_confirms_long and sol_confirms_long and not btc_disagrees_long):

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "cross_asset_trend_long_entry",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_close": current_close,
                        "btc_returns_recent": btc_returns_raw,
                        "sol_returns_recent": sol_returns_raw,
                    },
                )

            # Short condition: SOL fast EMA below slow EMA, BTC confirms, SOL confirms, BTC doesn't disagree
            elif (fast_ema < slow_ema and closes[-2] >= fast_ema and current_close < fast_ema and # Price crossed below fast EMA, fast EMA below slow EMA
                  btc_confirms_short and sol_confirms_short and not btc_disagrees_short):

                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "cross_asset_trend_short_entry",
                        "fast_ema": fast_ema,
                        "slow_ema": slow_ema,
                        "current_close": current_close,
                        "btc_returns_recent": btc_returns_raw,
                        "sol_returns_recent": sol_returns_raw,
                    },
                )

        # Exit logic (if holding a position)
        elif ctx.has_position():
            position_direction = ctx.position_direction()

            # BTC leader disagrees with our position, or SOL trend reverses
            if position_direction == "long":
                if btc_disagrees_long or fast_ema < slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "cross_asset_trend_long_exit",
                            "exit_condition": "btc_disagrees_or_sol_trend_reverses",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_close": current_close,
                            "btc_disagrees_long": btc_disagrees_long,
                        },
                    )
            elif position_direction == "short":
                if btc_disagrees_short or fast_ema > slow_ema:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "cross_asset_trend_short_exit",
                            "exit_condition": "btc_disagrees_or_sol_trend_reverses",
                            "fast_ema": fast_ema,
                            "slow_ema": slow_ema,
                            "current_close": current_close,
                            "btc_disagrees_short": btc_disagrees_short,
                        },
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close details or adapt sizing/parameters
        pass