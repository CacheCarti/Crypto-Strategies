from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 28800,  # 8 hours average hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.065       # -6.5% flush over 24h
        self.cooldown_bars = 10
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.panic_low = 0.0
        self.target_mid = 0.0

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)
        if len(closes) < 25:
            return None

        current_close = ctx.bar.close
        current_bar = ctx.bar_index

        # Position management
        if ctx.has_position():
            bars_held = current_bar - self.entry_bar
            # Midpoint take profit or time horizon cutoff
            if self.target_mid > 0 and current_close >= self.target_mid:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "reached_flush_midpoint", "price": current_close, "target": self.target_mid}
                )
            if bars_held >= 20:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_20_bars", "price": current_close, "bars_held": bars_held}
                )
            return None

        # Check post-exit cooldown
        if (current_bar - self.last_exit_bar) < self.cooldown_bars:
            return None

        # 1. Measure 24-bar rolling return on SOL
        ref_price = closes[-25]
        sol_24h_ret = (current_close - ref_price) / ref_price

        if sol_24h_ret > self.drop_thresh:
            return None

        # 2. Material divergence / cross-asset stabilization:
        # Cross-asset filter: BTC 24h return is not in an accelerating free-fall (e.g. btc_return_pct > -6.0)
        btc_24h = ctx.features.get("btc_return_pct", 0.0)
        if btc_24h < -6.0:
            return None

        # 3. Stabilization trigger: Bar closes above previous bar's high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # 4. Volatility / Oversold confirmation: RSI must be recovering from deep oversold (< 42)
        rsi = self._rsi(closes, period=14)
        if rsi > 42.0:
            return None

        # Dynamic Stop & Take Profit calculated from the panic flush range
        flush_low = min(lows[-self.lookback:])
        flush_high = max(highs[-self.lookback:])
        flush_range = flush_high - flush_low

        if flush_range <= 0:
            return None

        midpoint = flush_low + (flush_range * 0.50)
        if midpoint <= current_close:
            midpoint = current_close * 1.03  # fallback 3% target if already past mid

        # Dynamic SL based on panic low with buffer
        sl_dist = current_close - flush_low
        sl_bps = max(200.0, min(650.0, (sl_dist / current_close) * 10000.0 + 40.0))
        tp_bps = max(250.0, min(800.0, ((midpoint - current_close) / current_close) * 10000.0))

        self.panic_low = flush_low
        self.target_mid = midpoint
        self.entry_bar = current_bar

        fear_greed = ctx.features.get("fear_greed_index", 50)
        confidence = 0.75 if fear_greed < 35 else 0.60

        return ctx.signal(
            "long",
            confidence=confidence,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=28800,
            metadata={
                "reason": "sol_flush_stabilization_cross_asset_confirmed",
                "sol_24h_ret": round(sol_24h_ret, 4),
                "btc_24h": round(btc_24h, 2),
                "rsi": round(rsi, 2),
                "flush_low": flush_low,
                "midpoint_target": round(midpoint, 2),
                "fear_greed": fear_greed
            }
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.target_mid = 0.0
        self.panic_low = 0.0