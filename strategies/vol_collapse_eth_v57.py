from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 43200,  # 12 hours
        "warmup_bars": 220,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_period = 12
        self.median_period = 168
        self.breakout_period = 12
        self.spike_threshold = 1.8
        self.max_compression_window = 36
        self.cooldown_period = 14
        
        self.spike_active = False
        self.compressed = False
        self.compression_bar = -9999
        self.last_exit_bar = -9999
        self.entry_bar = -9999
        self.max_hold_bars = 24

    def _calc_rv(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            r = math.log(closes[i] / closes[i - 1])
            returns.append(r)
        mean_r = sum(returns) / period
        var = sum((r - mean_r) ** 2 for r in returns) / period
        return math.sqrt(var)

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(len(closes) - period, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.compressed = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.median_period + self.rv_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        # Check existing position management: timed structural exit to prevent allocation giveback chop
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_structural_exit", "bars_held": bars_held}
                )
            return None

        # Post-exit mandatory cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Calculate current RV
        curr_rv = self._calc_rv(closes, self.rv_period)
        if curr_rv is None or curr_rv <= 0:
            return None

        # Compute rolling RV history to determine median over 168 bars
        rv_hist = []
        # Sample rolling RV at interval 2 to keep execution lean and fast
        for idx in range(total_needed - self.median_period, total_needed + 1, 2):
            sub_c = closes[:idx]
            sub_rv = self._calc_rv(sub_c, self.rv_period)
            if sub_rv is not None:
                rv_hist.append(sub_rv)

        if len(rv_hist) < 20:
            return None

        sorted_rv = sorted(rv_hist)
        median_rv = sorted_rv[len(sorted_rv) // 2]
        if median_rv <= 0:
            return None

        # State transition: Spike -> Compression
        if curr_rv >= self.spike_threshold * median_rv:
            self.spike_active = True
            self.compressed = False

        if self.spike_active and curr_rv < median_rv:
            self.spike_active = False
            self.compressed = True
            self.compression_bar = ctx.bar_index

        # Expire compression window if coil fails to release within allowed bars
        if self.compressed and (ctx.bar_index - self.compression_bar > self.max_compression_window):
            self.compressed = False

        # Breakout entry evaluation
        if self.compressed:
            highs = ctx.highs(self.breakout_period + 2)
            lows = ctx.lows(self.breakout_period + 2)
            if len(highs) < self.breakout_period + 2:
                return None

            # Donchian channel reference excluding current forming bar
            prior_high = max(highs[-(self.breakout_period + 1):-1])
            prior_low = min(lows[-(self.breakout_period + 1):-1])
            price = ctx.bar.close

            atr_val = self._atr(ctx.highs(20), ctx.lows(20), ctx.closes(20), period=14)
            if atr_val is None or atr_val <= 0:
                atr_val = price * 0.015

            # Dynamic volatility stop calculation: 2.2x ATR converted to basis points
            dyn_sl_bps = max(180.0, min(380.0, (2.2 * atr_val / price) * 10000.0))
            dyn_tp_bps = dyn_sl_bps * 2.0

            if price > prior_high:
                self.compressed = False
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=0.8,
                    stop_loss_bps=dyn_sl_bps,
                    take_profit_bps=dyn_tp_bps,
                    metadata={
                        "reason": "coil_release_high_breakout",
                        "rv_ratio": round(curr_rv / median_rv, 3),
                        "prior_high": prior_high,
                        "close": price,
                        "dyn_sl_bps": round(dyn_sl_bps, 1)
                    }
                )
            elif price < prior_low:
                self.compressed = False
                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=0.8,
                    stop_loss_bps=dyn_sl_bps,
                    take_profit_bps=dyn_tp_bps,
                    metadata={
                        "reason": "coil_release_low_breakdown",
                        "rv_ratio": round(curr_rv / median_rv, 3),
                        "prior_low": prior_low,
                        "close": price,
                        "dyn_sl_bps": round(dyn_sl_bps, 1)
                    }
                )

        return None