from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthVolCoilBreakout(Strategy):
    METADATA = {
        "name": "EthVolCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 43200,  # 12 hours max swing
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_period = 168
        self.break_period = 12
        self.cooldown_bars = 14
        self.last_exit_bar = -100
        self.was_spiked = False
        self.coiled = False
        self.coil_bar = -100
        self.max_coil_age = 24  # coil valid for 24 bars (~1 day)

    def _realized_vol(self, closes, period=168):
        if len(closes) < period + 1:
            return None
        returns = []
        for i in range(len(closes) - period, len(closes)):
            r = math.log(closes[i] / closes[i - 1])
            returns.append(r)
        mean_r = sum(returns) / period
        var = sum((r - mean_r) ** 2 for r in returns) / (period - 1)
        return math.sqrt(var)

    def _median_vol_history(self, closes, period=168, hist_bars=168):
        # Rolling historical proxy of volatility median over recent window
        # To avoid heavy compute, sample 12 points spaced across past history
        if len(closes) < period + hist_bars:
            return None
        vols = []
        step = max(1, hist_bars // 12)
        for offset in range(0, hist_bars, step):
            sub_closes = closes[:len(closes) - offset]
            v = self._realized_vol(sub_closes, period=period)
            if v is not None:
                vols.append(v)
        if not vols:
            return None
        vols.sort()
        return vols[len(vols) // 2]

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.rv_period + 180)
        highs = ctx.highs(self.break_period + 2)
        lows = ctx.lows(self.break_period + 2)

        if len(closes) < self.rv_period + 60:
            return None

        # Check existing position - time stop or structure exit
        if ctx.has_position():
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Calculate current RV and baseline
        curr_rv = self._realized_vol(closes, period=self.rv_period)
        med_rv = self._median_vol_history(closes, period=self.rv_period, hist_bars=120)

        if curr_rv is None or med_rv is None or med_rv <= 0:
            return None

        vol_ratio = curr_rv / med_rv

        # State 1: Spike detection (> 1.7x baseline)
        if vol_ratio >= 1.7:
            self.was_spiked = True

        # State 2: Collapse into coil (was spiked, now drops back below 1.15x median)
        if self.was_spiked and vol_ratio < 1.15:
            self.coiled = True
            self.was_spiked = False
            self.coil_bar = ctx.bar_index

        # Age out stale coils
        if self.coiled and (ctx.bar_index - self.coil_bar > self.max_coil_age):
            self.coiled = False

        if not self.coiled:
            return None

        # Dynamic Donchian coil release trigger (past 12 bars excluding current)
        prior_highs = highs[-self.break_period - 1:-1]
        prior_lows = lows[-self.break_period - 1:-1]
        if len(prior_highs) < self.break_period or len(prior_lows) < self.break_period:
            return None

        upper_bound = max(prior_highs)
        lower_bound = min(prior_lows)
        curr_price = ctx.bar.close

        # Volatility-based adaptive stops
        # Use short-term ATR proxy for structure stop
        bar_ranges = [highs[i] - lows[i] for i in range(len(highs))]
        recent_range = sum(bar_ranges[-5:]) / 5.0
        range_bps = (recent_range / curr_price) * 10000.0 if curr_price > 0 else 250.0
        stop_bps = max(180.0, min(380.0, range_bps * 1.5))
        tp_bps = stop_bps * 2.0

        # Breakout signals
        if curr_price > upper_bound:
            self.coiled = False
            return ctx.signal(
                "long",
                confidence=0.80,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_upper_breakout",
                    "vol_ratio": round(vol_ratio, 3),
                    "upper_bound": upper_bound,
                    "curr_price": curr_price,
                    "stop_bps": round(stop_bps, 1)
                }
            )

        if curr_price < lower_bound:
            self.coiled = False
            return ctx.signal(
                "short",
                confidence=0.80,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_lower_breakout",
                    "vol_ratio": round(vol_ratio, 3),
                    "lower_bound": lower_bound,
                    "curr_price": curr_price,
                    "stop_bps": round(stop_bps, 1)
                }
            )

        return None