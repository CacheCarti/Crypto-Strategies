from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolEthCrossAssetTrend(Strategy):
    METADATA = {
        "name": "SOL-ETH Cross-Asset Trend",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2 hours avg hold
        "warmup_bars": 60, # Need enough for 2 * trend_period
        "required_features": ["sol_return_pct", "eth_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.trend_period = 30 # For EMA to detect trend
        self.cooldown_bars = 5
        self.last_signal_bar = -self.cooldown_bars

        # Store previous trend states to detect transitions
        self.prev_eth_ema = None
        self.prev_sol_ema = None

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.trend_period + 1)
        if len(closes) < self.trend_period + 1:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_signal_bar + self.cooldown_bars:
            return None

        # Calculate EMAs for trend detection
        eth_ema = self._ema(closes, self.trend_period)
        if eth_ema is None:
            return None

        # Get fresh 24h returns for cross-asset confirmation
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        eth_return_pct = ctx.features.get("eth_return_pct", 0.0)

        # Ensure we have previous EMA values for transition detection
        if self.prev_eth_ema is None or self.prev_sol_ema is None:
            self.prev_eth_ema = eth_ema
            self.prev_sol_ema = self._ema(ctx.features.get("sol_return_pct", [0.0]*self.trend_period), self.trend_period) # Placeholder for first bar
            return None

        current_price = ctx.bar.close

        # ETH trend: price above EMA
        eth_bullish_trend = current_price > eth_ema
        eth_bearish_trend = current_price < eth_ema

        # SOL trend: recent return for direction
        sol_bullish_momentum = sol_return_pct > 0.5 # SOL up > 0.5%
        sol_bearish_momentum = sol_return_pct < -0.5 # SOL down < -0.5%

        # Entry logic
        if not ctx.has_position():
            # Long entry: ETH bullish trend, SOL bullish momentum, and ETH confirms with positive return
            if eth_bullish_trend and sol_bullish_momentum and eth_return_pct > 0.1:
                # Transition detection: only enter if ETH just crossed above its EMA or SOL just turned positive
                eth_crossed_up = (self.prev_eth_ema is not None and ctx.closes(2)[0] < self.prev_eth_ema and current_price > eth_ema)
                sol_turned_positive = (self.prev_sol_ema is not None and self.prev_sol_ema < 0 and sol_return_pct > 0)
                
                if eth_crossed_up or sol_turned_positive:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=0.7,
                        metadata={
                            "reason": "sol_eth_cross_asset_bullish_trend_confirm",
                            "eth_price": current_price,
                            "eth_ema": eth_ema,
                            "sol_return_pct": sol_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "eth_crossed_up": eth_crossed_up,
                            "sol_turned_positive": sol_turned_positive,
                        }
                    )

            # Short entry: ETH bearish trend, SOL bearish momentum, and ETH confirms with negative return
            elif eth_bearish_trend and sol_bearish_momentum and eth_return_pct < -0.1:
                # Transition detection: only enter if ETH just crossed below its EMA or SOL just turned negative
                eth_crossed_down = (self.prev_eth_ema is not None and ctx.closes(2)[0] > self.prev_eth_ema and current_price < eth_ema)
                sol_turned_negative = (self.prev_sol_ema is not None and self.prev_sol_ema > 0 and sol_return_pct < 0)

                if eth_crossed_down or sol_turned_negative:
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=0.7,
                        metadata={
                            "reason": "sol_eth_cross_asset_bearish_trend_confirm",
                            "eth_price": current_price,
                            "eth_ema": eth_ema,
                            "sol_return_pct": sol_return_pct,
                            "eth_return_pct": eth_return_pct,
                            "eth_crossed_down": eth_crossed_down,
                            "sol_turned_negative": sol_turned_negative,
                        }
                    )

        # Exit logic
        elif ctx.has_position():
            pos_dir = ctx.position_direction()
            entry_price = ctx.position["entry_price"]

            # Exit if ETH trend reverses or SOL momentum disagrees
            if pos_dir == "long":
                if current_price < eth_ema or sol_return_pct < -0.1: # ETH below EMA or SOL turns negative
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "long_exit_trend_reversal_or_sol_disagreement",
                            "eth_price": current_price,
                            "eth_ema": eth_ema,
                            "sol_return_pct": sol_return_pct,
                            "entry_price": entry_price,
                        }
                    )
            elif pos_dir == "short":
                if current_price > eth_ema or sol_return_pct > 0.1: # ETH above EMA or SOL turns positive
                    self.last_signal_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "short_exit_trend_reversal_or_sol_disagreement",
                            "eth_price": current_price,
                            "eth_ema": eth_ema,
                            "sol_return_pct": sol_return_pct,
                            "entry_price": entry_price,
                        }
                    )
        
        # Update previous EMA values for next bar's transition detection
        self.prev_eth_ema = eth_ema
        self.prev_sol_ema = sol_return_pct # Using current sol_return_pct directly for its "EMA"

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, just reset the last signal bar to allow for new entries
        # self.last_signal_bar = ctx.bar_index # This is handled by on_bar's cooldown
        pass