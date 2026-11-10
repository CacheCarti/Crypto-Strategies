from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours target hold
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.min_drop_pct = 0.055       # High-conviction 5.5% drop
        self.cooldown_bars = 16         # Strict post-trade cooldown
        self.last_exit_bar = -100
        self.target_exit_price = 0.0

    def _rsi(self, closes, period=14):
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
        self.last_exit_bar = ctx.bar_index
        self.target_exit_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)
        volumes = ctx.volumes(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        # Position Management
        if ctx.has_position():
            # Exit near the flush midpoint target or if profit target achieved
            if self.target_exit_price > 0 and current_close >= self.target_exit_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "target_price": self.target_exit_price,
                        "close": current_close
                    }
                )
            return None

        # Cooldown guard
        if current_bar - self.last_exit_bar < self.cooldown_bars:
            return None

        # Flush Detection over the lookback window
        recent_high = max(highs[-self.lookback:])
        recent_low = min(lows[-self.lookback:])
        
        if recent_high <= 0:
            return None

        drawdown = (recent_high - recent_low) / recent_high
        net_drop = (recent_high - current_close) / recent_high

        # Real flush required: at least 5.5% drop from the local peak
        if net_drop < self.min_drop_pct:
            return None

        # Volume capitulation confirmation: local low bar must have elevated volume
        avg_vol = sum(volumes[-self.lookback:]) / self.lookback
        curr_vol = ctx.bar.volume
        if curr_vol < avg_vol * 1.1:
            return None

        # Oversold exhaustion check
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 42.0:
            return None

        # Stabilization trigger: current close breaks above previous bar's high
        prev_high = highs[-2]
        if current_close > prev_high:
            midpoint = (recent_high + recent_low) / 2.0
            
            # Ensure sufficient upside to midpoint target to clear friction (>100 bps)
            potential_reward_bps = ((midpoint - current_close) / current_close) * 10000.0
            if potential_reward_bps < 120.0:
                return None

            self.target_exit_price = midpoint
            confidence = min(0.95, max(0.55, (net_drop / 0.10)))

            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "flush_exhaustion_stabilization_long",
                    "drawdown_pct": round(net_drop * 100, 2),
                    "rsi": round(rsi_val, 2),
                    "target_midpoint": round(midpoint, 2),
                    "prev_high": round(prev_high, 2),
                    "close": current_close
                }
            )

        return None