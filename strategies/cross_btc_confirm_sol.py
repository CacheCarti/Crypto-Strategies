from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetTrendConfirm(Strategy):
    METADATA = {
        "name": "Cross Asset Trend Confirmation SOL",
        "domain": "sol_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 3,  # Target 3-hour hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "sol_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.btc_trend_period = 5
        self.sol_trend_period = 5
        self.cooldown_bars = 12  # Wait 12 bars (12 hours) after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.btc_trend_period + 1) # Need one more for returns
        
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Get BTC and SOL returns
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)

        # BTC as the leader: check its recent trend (e.g., positive return)
        btc_leader_bullish = btc_return_pct > 0.05  # BTC up by more than 0.05% in the last bar
        btc_leader_bearish = btc_return_pct < -0.05 # BTC down by more than 0.05% in the last bar

        # SOL as the target: confirm with its own recent trend
        sol_target_bullish = sol_return_pct > 0.05 # SOL up by more than 0.05% in the last bar
        sol_target_bearish = sol_return_pct < -0.05 # SOL down by more than 0.05% in the last bar

        # Define close-confirmed entry: current close must confirm the direction
        current_close = ctx.bar.close
        previous_close = ctx.closes(2)[0] if len(ctx.closes(2)) == 2 else current_close

        close_confirms_up = current_close > previous_close
        close_confirms_down = current_close < previous_close

        if not ctx.has_position():
            # Long entry condition
            if btc_leader_bullish and sol_target_bullish and close_confirms_up:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.7,
                    metadata={
                        "reason": "btc_sol_bullish_confirmation",
                        "btc_return_pct": btc_return_pct,
                        "sol_return_pct": sol_return_pct,
                        "current_close": current_close,
                        "previous_close": previous_close,
                    }
                )

            # Short entry condition
            if btc_leader_bearish and sol_target_bearish and close_confirms_down:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.7,
                    metadata={
                        "reason": "btc_sol_bearish_confirmation",
                        "btc_return_pct": btc_return_pct,
                        "sol_return_pct": sol_return_pct,
                        "current_close": current_close,
                        "previous_close": previous_close,
                    }
                )
        else:
            # Exit conditions
            # Leader disagrees or target trend normalizes
            if ctx.position_direction() == "long":
                # Exit if BTC turns bearish or SOL's positive return fades
                if btc_leader_bearish or sol_return_pct < 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "long_exit_btc_sol_disagreement_or_normalization",
                            "btc_return_pct": btc_return_pct,
                            "sol_return_pct": sol_return_pct,
                            "entry_price": ctx.position["entry_price"],
                            "current_close": current_close,
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit if BTC turns bullish or SOL's negative return fades
                if btc_leader_bullish or sol_return_pct > 0.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={
                            "reason": "short_exit_btc_sol_disagreement_or_normalization",
                            "btc_return_pct": btc_return_pct,
                            "sol_return_pct": sol_return_pct,
                            "entry_price": ctx.position["entry_price"],
                            "current_close": current_close,
                        }
                    )
        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Reset last_trade_bar to enforce cooldown after any position closure
        self.last_trade_bar = ctx.bar_index
        pass