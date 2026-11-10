from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthLagBtc(Strategy):
    METADATA = {
        "name": "EthLagBtc",
        "domain": "btc_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 7200, # 2 hours
        "warmup_bars": 50,
        "required_features": ["eth_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.eth_lookback = 3 # Bars for ETH decisive move
        self.btc_lookback = 1 # Bars for BTC not caught up
        self.min_eth_move_pct = 0.5 # Minimum % move for ETH to be considered decisive
        self.max_btc_lag_pct = 0.1 # Maximum % BTC can move in same direction to be considered lagging
        self.cooldown_bars = 5
        self.last_trade_bar = -self.cooldown_bars

        self.ema_period = 20 # For trend filtering
        self.min_volatility_regime = "normal" # Only trade in normal or higher volatility
        self.max_crisis_score = 0.5 # Stand down if crisis score is too high

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.ema_period * 2) # Get enough data for EMA
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None
        
        # Regime filters
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        
        if regime in ["CRISIS", "MELTDOWN"] or crisis_score > self.max_crisis_score:
            return None
        
        if regime == "CALM": # Only trade in normal or higher volatility
            return None

        current_price = ctx.bar.close
        ema = self._ema(closes, self.ema_period)
        if ema is None:
            return None

        # Get ETH and BTC recent returns
        eth_returns = [ctx.features.get("eth_return_pct", 0.0) for _ in range(self.eth_lookback)]
        btc_returns = [ctx.features.get("btc_return_pct", 0.0) for _ in range(self.btc_lookback)]
        
        # Calculate recent average returns
        avg_eth_return = sum(eth_returns[-self.eth_lookback:]) / self.eth_lookback
        avg_btc_return = sum(btc_returns[-self.btc_lookback:]) / self.btc_lookback

        signal_direction = None
        confidence = 0.0
        reason = ""
        
        # Long condition: ETH moves up decisively, BTC lags
        if avg_eth_return > self.min_eth_move_pct and avg_btc_return < self.max_btc_lag_pct:
            # Add trend filter: only long if price is above EMA
            if current_price > ema:
                signal_direction = "long"
                confidence = min(0.8, abs(avg_eth_return) / (self.min_eth_move_pct * 2) + 0.3)
                reason = "eth_leading_btc_lag_bullish"
        
        # Short condition: ETH moves down decisively, BTC lags
        elif avg_eth_return < -self.min_eth_move_pct and avg_btc_return > -self.max_btc_lag_pct:
            # Add trend filter: only short if price is below EMA
            if current_price < ema:
                signal_direction = "short"
                confidence = min(0.8, abs(avg_eth_return) / (self.min_eth_move_pct * 2) + 0.3)
                reason = "eth_leading_btc_lag_bearish"

        if ctx.has_position():
            # If we have a position, check if the relationship normalizes or reverses
            if ctx.position_direction() == "long":
                # Exit long if ETH return turns negative or BTC catches up too much
                if avg_eth_return < 0 or avg_btc_return > self.min_eth_move_pct / 2:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "long_exit_eth_reversal_or_btc_catchup",
                        "eth_return": avg_eth_return,
                        "btc_return": avg_btc_return,
                        "current_price": current_price
                    })
            elif ctx.position_direction() == "short":
                # Exit short if ETH return turns positive or BTC catches up too much
                if avg_eth_return > 0 or avg_btc_return < -self.min_eth_move_pct / 2:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal("flat", confidence=0.6, metadata={
                        "reason": "short_exit_eth_reversal_or_btc_catchup",
                        "eth_return": avg_eth_return,
                        "btc_return": avg_btc_return,
                        "current_price": current_price
                    })
            
            # If current signal wants to flip, or no signal but position exists
            if signal_direction and signal_direction != ctx.position_direction():
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(signal_direction, confidence=confidence, metadata={
                    "reason": f"position_flip_{reason}",
                    "eth_return": avg_eth_return,
                    "btc_return": avg_btc_return,
                    "current_price": current_price,
                    "ema": ema
                })
            
            return None # Hold position if no exit condition met and no flip signal

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": reason,
                    "eth_return": avg_eth_return,
                    "btc_return": avg_btc_return,
                    "current_price": current_price,
                    "ema": ema,
                    "regime": regime
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # We already handle cooldown with self.last_trade_bar in on_bar
        pass