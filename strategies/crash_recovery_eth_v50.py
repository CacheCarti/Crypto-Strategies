from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthPanicFlushRecovery(Strategy):
    METADATA = {
        "name": "ETH Panic Flush Mean Reversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh_pct = -0.045
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 1:
            return None

        # Position Management
        if ctx.has_position():
            midpoint = (self.flush_high + self.flush_low) / 2.0
            cur_price = ctx.bar.close

            # Mean-reversion exit target reached
            if self.flush_high > self.flush_low and cur_price >= midpoint:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "flush_midpoint_reversion_target_hit",
                        "price": cur_price,
                        "midpoint": midpoint,
                        "flush_low": self.flush_low,
                    },
                )
            return None

        # Mandatory cooldown check
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Condition 1: Substantial drop over past 24 bars
        drop_pct = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback]
        if drop_pct > self.drop_thresh_pct:
            return None

        # Condition 2: Structural stabilization (bar closes strictly above previous bar's high)
        if ctx.bar.close <= highs[-2]:
            return None

        # Condition 3: Volatility & Cross-asset confirmation (different from failed pure-candle setups)
        # Verify sell-off exhaustion via volume expansion or BTC stabilization
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        atr_val = self._atr(highs, lows, closes, 14)
        recent_range = ctx.bar.high - ctx.bar.low

        # Cross-asset filter: don't catch knife if BTC is crashing severely (> 4% daily drop)
        if btc_ret < -4.0:
            return None

        # Volatility expansion check: recovery bar must show committed reversal range
        if atr_val is not None and recent_range < (0.6 * atr_val):
            return None

        # Record flush reference levels
        self.flush_high = max(highs[-self.lookback:])
        self.flush_low = min(lows[-self.lookback:])

        # Measure dynamic SL based on flush low
        risk_bps = ((ctx.bar.close - self.flush_low) / ctx.bar.close) * 10000.0
        stop_bps = max(180.0, min(risk_bps + 40.0, 360.0))
        target_bps = max(260.0, min((((self.flush_high + self.flush_low) / 2.0 - ctx.bar.close) / ctx.bar.close) * 10000.0, 520.0))

        confidence = 0.75 if drop_pct < -0.065 else 0.65

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=stop_bps,
            take_profit_bps=target_bps,
            horizon_seconds=21600,
            metadata={
                "reason": "panic_flush_stabilization_cross_confirm",
                "drop_24h_pct": round(drop_pct * 100, 2),
                "btc_24h_return": btc_ret,
                "flush_high": self.flush_high,
                "flush_low": self.flush_low,
                "entry_price": ctx.bar.close,
            },
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index