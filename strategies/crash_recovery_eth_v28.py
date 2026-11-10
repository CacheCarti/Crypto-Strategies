from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "EthPanicFlushRecovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 360.0,
        "declared_hold_seconds": 18000,  # ~5 hours target recovery
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.drop_lookback = 24
        self.min_drop_pct = 0.045       # 4.5% drop over 24h to trigger flush detection
        self.cooldown_bars = 10         # Wait after position close
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.target_price = 0.0
        self.stop_price = 0.0

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
        self.target_price = 0.0
        self.stop_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.drop_lookback + 5)
        highs = ctx.highs(self.drop_lookback + 5)
        lows = ctx.lows(self.drop_lookback + 5)

        if len(closes) < self.drop_lookback + 1:
            return None

        current_close = ctx.bar.close
        has_pos = ctx.has_position()

        # Manage existing long position
        if has_pos:
            bars_held = ctx.bar_index - self.entry_bar
            # Midpoint target reached or time-stop
            if self.target_price > 0.0 and current_close >= self.target_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "price": current_close,
                        "target_price": self.target_price,
                        "bars_held": bars_held
                    }
                )
            if bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "flush_recovery_time_horizon_reached",
                        "price": current_close,
                        "bars_held": bars_held
                    }
                )
            return None

        # Cooldown enforcement
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter based on prior evidence: avoid Meltdown regimes where knives keep falling
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime == "MELTDOWN" or crisis_score > 0.85:
            return None

        # Look for a 4.5%+ flush over the last 24 bars
        recent_high = max(highs[-self.drop_lookback:])
        flush_low = min(lows[-self.drop_lookback:])
        if recent_high <= 0:
            return None

        drop_pct = (recent_high - current_close) / recent_high

        # 1. Price flushed >= 4.5%
        # 2. Stabilization confirmed: current close > previous bar high
        # 3. RSI confirms bounce out of deep oversold (< 42)
        prev_high = highs[-2]
        if drop_pct >= self.min_drop_pct and current_close > prev_high:
            rsi_val = self._rsi(closes, period=14)
            if rsi_val is not None and rsi_val < 44.0:
                # Target is near the midpoint of the flush range (50% retracement)
                midpoint = flush_low + 0.50 * (recent_high - flush_low)
                
                # Ensure the target provides a minimum attractive upside buffer
                if midpoint > current_close * 1.01:
                    self.entry_bar = ctx.bar_index
                    self.target_price = midpoint

                    # Dynamic stop slightly below the recent flush swing low
                    stop_dist_bps = max(180.0, min(320.0, ((current_close - flush_low) / current_close) * 10000.0 + 40.0))
                    tp_dist_bps = max(240.0, min(450.0, ((midpoint - current_close) / current_close) * 10000.0))

                    conf = 0.65 if drop_pct < 0.07 else 0.80

                    return ctx.signal(
                        "long",
                        confidence=conf,
                        stop_loss_bps=stop_dist_bps,
                        take_profit_bps=tp_dist_bps,
                        horizon_seconds=18000,
                        metadata={
                            "reason": "panic_flush_stabilization_bounce",
                            "drop_pct": round(drop_pct * 100.0, 2),
                            "rsi": round(rsi_val, 2),
                            "flush_low": round(flush_low, 2),
                            "recent_high": round(recent_high, 2),
                            "midpoint_target": round(midpoint, 2),
                            "regime": market_regime
                        }
                    )

        return None