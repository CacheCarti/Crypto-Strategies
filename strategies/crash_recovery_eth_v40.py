from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "ETH Panic Flush Recovery",
        "domain": "eth_usdc",
        "declared_sl_bps": 400.0,
        "declared_tp_bps": 320.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.05
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_g = sum(gains[-period:]) / period
        avg_l = sum(losses[-period:]) / period
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.target_price = 0.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close

        # Manage existing long position
        if ctx.has_position():
            if ctx.position_direction() == "long":
                # Exit near midpoint of flush or if regime reaches crisis meltdown
                if self.target_price > 0.0 and current_close >= self.target_price:
                    return ctx.signal(
                        "flat",
                        confidence=0.7,
                        metadata={
                            "reason": "flush_midpoint_reached",
                            "target": self.target_price,
                            "close": current_close
                        }
                    )
            return None

        # Cooldown guard
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Calculate 24-bar drop
        ref_price = closes[-self.lookback]
        rolling_return = (current_close - ref_price) / ref_price

        # Check for deep panic flush
        if rolling_return <= self.drop_thresh:
            recent_high = max(highs[-self.lookback:])
            recent_low = min(lows[-self.lookback:])
            prev_high = highs[-2]

            # Structural stabilization: current close breaks above previous bar's high
            is_stabilized = current_close > prev_high

            # Cross-asset & volatility confirmation (divergence from extreme collapse)
            btc_ret = ctx.features.get("btc_return_pct", 0.0)
            fg_index = ctx.features.get("fear_greed_index", 50.0)
            rsi = self._rsi(closes, period=14)
            regime = ctx.market.get("regime", "NORMAL")

            # Must not be an outright untreatable meltdown; BTC shouldn't be in freefall cascade
            cross_asset_ok = btc_ret > -0.09
            oversold_exhaustion = rsi < 42.0

            if is_stabilized and cross_asset_ok and oversold_exhaustion and regime != "MELTDOWN":
                self.flush_high = recent_high
                self.flush_low = recent_low
                # Target midpoint of the flush
                self.target_price = (recent_high + recent_low) / 2.0

                # Ensure positive reward-to-risk toward midpoint
                if self.target_price > current_close * 1.01:
                    confidence = 0.65
                    if fg_index < 25.0:
                        confidence += 0.15

                    return ctx.signal(
                        "long",
                        confidence=min(confidence, 0.95),
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        metadata={
                            "reason": "panic_flush_reversal_confirmed",
                            "rolling_return": rolling_return,
                            "rsi": rsi,
                            "btc_ret": btc_ret,
                            "midpoint_target": self.target_price,
                            "fear_greed": fg_index
                        }
                    )

        return None