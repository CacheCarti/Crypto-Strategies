from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PanicRecoveryETH(Strategy):
    METADATA = {
        "name": "PanicRecoveryETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.05
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.target_price = 0.0
        self.entry_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        
        if len(closes) < self.lookback + 2:
            return None

        # Check existing position for take-profit at the flush midpoint
        if ctx.has_position():
            if ctx.position_direction() == "long":
                current_price = ctx.bar.close
                # If target price reached, take profit to secure mean reversion
                if self.target_price > 0 and current_price >= self.target_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "flush_midpoint_target_hit",
                            "price": current_price,
                            "target_price": self.target_price,
                            "entry_price": self.entry_price,
                        }
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # 1. Panic Drop Detection: drop >= 5% over the last 24 bars
        anchor_price = closes[-self.lookback]
        current_close = ctx.bar.close
        rolling_return = (current_close - anchor_price) / anchor_price
        
        if rolling_return > self.drop_thresh:
            return None

        # 2. Material Stabilization: Bar closes above previous bar's high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # 3. Cross-Asset & Volatility State Confirmation (addressing prior failure mode)
        # Avoid freefalling traps: BTC 24h return must show stabilization (> -8% panic limit)
        # and RSI must confirm genuine oversold bounce (RSI < 42)
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        rsi = self._rsi(closes, period=14)
        
        if btc_ret < -8.0:
            return None  # Cascading macro liquidation, avoid catching the knife
            
        if rsi is None or rsi > 42.0:
            return None  # Only buy verified deep discount bounces

        # Calculate range midpoint target: average of the peak before drop and trough
        flush_high = max(highs[-self.lookback:])
        flush_low = min(lows[-self.lookback:])
        midpoint = (flush_high + flush_low) / 2.0
        
        # Verify sufficient upside remaining to midpoint
        if midpoint <= current_close * 1.01:
            return None

        self.target_price = midpoint
        self.entry_price = current_close

        # Dynamic confidence based on oversold depth
        confidence = min(0.9, max(0.5, 0.5 + abs(rolling_return) * 2.0))

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=350.0,
            take_profit_bps=500.0,
            horizon_seconds=28800,
            metadata={
                "reason": "panic_flush_reversal_confirmed",
                "rolling_return": rolling_return,
                "rsi": rsi,
                "btc_return_pct": btc_ret,
                "flush_high": flush_high,
                "flush_low": flush_low,
                "target_midpoint": midpoint,
                "entry_price": current_close,
            }
        )