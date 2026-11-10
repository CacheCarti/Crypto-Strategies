from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,  # ~6 hours hold horizon
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_threshold_pct = 0.045  # 4.5% drop over 24h window
        self.cooldown_period = 8          # wait at least 8 bars between trades
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0
        self.bars_in_trade = 0

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
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_idx = ctx.bar_index

        # Position Management & Dynamic Midpoint Exit
        if ctx.has_position():
            self.bars_in_trade += 1
            
            # Midpoint take-profit or time-based exhaustion
            if self.target_price > 0 and current_close >= self.target_price:
                self.target_price = 0.0
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "close": current_close,
                        "bars_held": self.bars_in_trade
                    }
                )

            # Exit if recovery stalls over 12 bars (~12 hours)
            if self.bars_in_trade >= 12:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "flush_recovery_time_decay_exit",
                        "close": current_close,
                        "bars_held": self.bars_in_trade
                    }
                )
            return None

        # Cooldown guard to avoid churn after stopping or exiting
        if (current_idx - self.last_exit_bar) < self.cooldown_period:
            return None

        # Detect flush: 4.5%+ drop across lookback window
        window_high = max(highs[-self.lookback:])
        window_low = min(lows[-self.lookback:])
        recent_drop = (window_high - current_close) / window_high

        if recent_drop < self.drop_threshold_pct:
            return None

        # Stabilization confirmation:
        # Current bar closes above previous bar's high (micro-reversal)
        prev_high = highs[-2]
        is_stabilized = current_close > prev_high

        if not is_stabilized:
            return None

        # RSI filter: must be recovering from oversold, not already fully overbought
        rsi_val = self._rsi(closes, period=14)
        if rsi_val is None or rsi_val > 55.0:
            return None

        # Skip entries if the market context indicates meltdown
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Calculate flush range midpoint target
        self.flush_high = window_high
        self.flush_low = min(lows[-self.lookback:])
        self.target_price = (self.flush_high + self.flush_low) / 2.0
        self.bars_in_trade = 0

        # Confidence scaled by depth of flush
        conf = min(0.9, max(0.55, 0.5 + (recent_drop * 3.0)))

        return ctx.signal(
            "long",
            confidence=conf,
            stop_loss_bps=280.0,
            take_profit_bps=380.0,
            horizon_seconds=21600,
            metadata={
                "reason": "panic_flush_stabilization_long",
                "drop_pct": round(recent_drop * 100, 2),
                "rsi": round(rsi_val, 2),
                "target_midpoint": round(self.target_price, 2),
                "flush_high": round(self.flush_high, 2),
                "flush_low": round(self.flush_low, 2)
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_price = 0.0
        self.bars_in_trade = 0