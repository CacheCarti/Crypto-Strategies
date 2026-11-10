from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolPanicRecovery(Strategy):
    METADATA = {
        "name": "SolPanicRecovery",
        "domain": "sol_usdc",
        "declared_sl_bps": 450.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
        "required_features": ["btc_return_pct", "fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.drop_thresh = -0.065       # 6.5% flush over 24h
        self.cooldown_bars = 12         # wait 12 bars after trade before re-entering
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.flush_high = 0.0
        self.flush_low = 0.0
        self.tp_price = 0.0
        self.sl_price = 0.0

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
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        current_close = ctx.bar.close
        current_idx = ctx.bar_index

        # Position management
        if ctx.has_position():
            bars_held = current_idx - self.entry_bar
            # Exit near the flush-range midpoint or hit target
            if current_close >= self.tp_price:
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "flush_midpoint_tp_reached",
                        "price": current_close,
                        "tp_target": self.tp_price,
                        "bars_held": bars_held,
                    },
                )
            # Stop-out below panic low
            if current_close <= self.sl_price:
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "panic_low_sl_hit",
                        "price": current_close,
                        "sl_target": self.sl_price,
                        "bars_held": bars_held,
                    },
                )
            # Time-based stale trade release
            if bars_held >= 18:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_timeout",
                        "price": current_close,
                        "bars_held": bars_held,
                    },
                )
            return None

        # Cooldown guard
        if current_idx - self.last_exit_bar < self.cooldown_bars:
            return None

        # Check for crash condition over lookback (24 bars)
        start_price = closes[-self.lookback]
        rolling_return = (current_close - start_price) / start_price

        if rolling_return > self.drop_thresh:
            return None

        # Stabilization confirmation: Close crosses above previous bar high
        prev_high = highs[-2]
        if current_close <= prev_high:
            return None

        # Novel confirmation: Cross-asset BTC confirmation + RSI oversold recovery
        # Avoid free-fall when BTC is actively in a cascade
        btc_ret_24h = ctx.features.get("btc_return_pct", 0.0)
        rsi = self._rsi(closes, period=14)

        # Cross-asset filter: BTC 24h return shouldn't be worse than -10% (avoid outright market breakdown)
        # RSI must be recovering from deep oversold (< 42)
        if btc_ret_24h < -10.0 or rsi > 42.0:
            return None

        # Structure metrics: Midpoint TP and stop below panic swing low
        period_lows = lows[-self.lookback:]
        period_highs = highs[-self.lookback:]
        self.flush_low = min(period_lows)
        self.flush_high = max(period_highs)

        midpoint = (self.flush_high + self.flush_low) / 2.0
        self.tp_price = max(midpoint, current_close * 1.025)
        # Low stop: just below the flush low, bounded realistically
        raw_sl = self.flush_low * 0.995
        self.sl_price = max(raw_sl, current_close * (1.0 - 0.045))

        calc_sl_bps = ((current_close - self.sl_price) / current_close) * 10000.0
        calc_tp_bps = ((self.tp_price - current_close) / current_close) * 10000.0

        sl_bps = min(max(calc_sl_bps, 200.0), 500.0)
        tp_bps = min(max(calc_tp_bps, 250.0), 650.0)

        self.entry_bar = current_idx

        return ctx.signal(
            "long",
            confidence=0.85,
            stop_loss_bps=sl_bps,
            take_profit_bps=tp_bps,
            horizon_seconds=18000,
            metadata={
                "reason": "sol_flush_stabilization_cross_confirm",
                "rolling_return_pct": rolling_return * 100.0,
                "rsi": rsi,
                "btc_return_pct": btc_ret_24h,
                "flush_low": self.flush_low,
                "flush_high": self.flush_high,
                "midpoint_target": midpoint,
            },
        )

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index