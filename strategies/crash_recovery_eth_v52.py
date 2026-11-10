from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold horizon
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.045         # -4.5% drop requirement
        self.cooldown_bars = 10           # Cooldown after exits
        self.max_hold_bars = 16           # Max bars to hold
        self.bars_since_exit = 20
        self.bars_in_trade = 0
        self.target_price = 0.0
        self.entry_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        self.bars_since_exit = 0
        self.bars_in_trade = 0
        self.target_price = 0.0
        self.entry_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        self.bars_since_exit += 1
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Manage active position exit via target price midpoint or time stop
        if ctx.has_position():
            self.bars_in_trade += 1
            curr_close = closes[-1]
            if self.target_price > 0 and curr_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_target_reached",
                        "price": curr_close,
                        "target_price": self.target_price,
                        "bars_held": self.bars_in_trade
                    }
                )
            if self.bars_in_trade >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_hold_expired",
                        "price": curr_close,
                        "bars_held": self.bars_in_trade
                    }
                )
            return None

        # Skip entry if under cooldown
        if self.bars_since_exit < self.cooldown_bars:
            return None

        # Check for deep drop over the last 24 bars
        recent_high = max(highs[-self.lookback:-1])
        prior_close = closes[-2]
        curr_close = closes[-1]
        prior_high = highs[-2]

        if recent_high <= 0:
            return None

        # Drop from the 24-bar high to the prior bar's close
        flush_drop = (prior_close - recent_high) / recent_high

        # Entry condition:
        # 1. 24-bar drop exceeds threshold (-4.5%)
        # 2. Stabilization candle: current close breaks above prior bar's high
        # 3. Oversold recovery confirmation: RSI(14) was depressed but turning (< 40)
        rsi_val = self._rsi(closes, period=14)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        if flush_drop <= self.drop_thresh and curr_close > prior_high and rsi_val < 42.0 and crisis_score < 0.85:
            recent_low = min(lows[-self.lookback:])
            # Midpoint of the flush range as profit target
            target = recent_low + (recent_high - recent_low) * 0.48
            
            # Confidence scaled by depth of drop
            drop_mag = abs(flush_drop)
            conf = min(0.9, max(0.6, drop_mag * 10.0))

            self.target_price = target
            self.entry_price = curr_close

            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=280.0,
                take_profit_bps=360.0,
                horizon_seconds=21600,
                metadata={
                    "reason": "flush_rebound_above_prior_high",
                    "flush_drop_pct": round(flush_drop * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "price": curr_close,
                    "target_midpoint": round(target, 2),
                    "recent_high": round(recent_high, 2),
                    "recent_low": round(recent_low, 2)
                }
            )

        return None