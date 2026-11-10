from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BetaConvergenceEth(Strategy):
    METADATA = {
        "name": "Beta Convergence ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4, # 4-hour average hold
        "warmup_bars": 50,
        "required_features": ["btc_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.ema_period_short = 10
        self.ema_period_long = 30
        self.cooldown_bars = 5 # Cooldown after a trade
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
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Check for significant BTC move
        btc_move_threshold = 0.5 # 0.5% move in BTC
        eth_lag_threshold = 0.2 # ETH has moved less than 0.2% in the same direction

        # Calculate ETH's short-term momentum using EMAs
        ema_short = self._ema(closes, self.ema_period_short)
        ema_long = self._ema(closes, self.ema_period_long)

        if ema_short is None or ema_long is None:
            return None

        current_price = ctx.bar.close

        # Entry logic: BTC makes a decisive move, ETH lags, and ETH is trending in the direction of the BTC move
        if not ctx.has_position():
            # Long entry condition
            if btc_return_pct > btc_move_threshold and eth_return_pct < btc_return_pct * eth_lag_threshold:
                # Ensure ETH is also in an uptrend (EMA short above EMA long)
                if ema_short > ema_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "btc_lead_eth_lag_bullish_ema_cross",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "current_price": current_price
                        }
                    )
            # Short entry condition
            elif btc_return_pct < -btc_move_threshold and eth_return_pct > btc_return_pct * eth_lag_threshold:
                # Ensure ETH is also in a downtrend (EMA short below EMA long)
                if ema_short < ema_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.75,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "btc_lead_eth_lag_bearish_ema_cross",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "current_price": current_price
                        }
                    )
        
        # Exit logic: if position exists, check for normalization or trend reversal
        elif ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit if ETH starts to catch up or trend reverses
                if eth_return_pct > btc_return_pct * 0.8 or ema_short < ema_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_position_eth_catchup_or_ema_bearish_cross",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "current_price": current_price
                        }
                    )
            elif ctx.position_direction() == "short":
                # Exit if ETH starts to catch up or trend reverses
                if eth_return_pct < btc_return_pct * 0.8 or ema_short > ema_long:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_position_eth_catchup_or_ema_bullish_cross",
                            "btc_return_pct": btc_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "current_price": current_price
                        }
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: log position close for analysis or adaptive sizing
        pass