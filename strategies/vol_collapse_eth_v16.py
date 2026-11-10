from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthVolCoilBreakout(Strategy):
    METADATA = {
        "name": "EthVolCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 190,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 14
        self.hist_period = 168
        self.breakout_period = 12
        self.cooldown_period = 8
        self.max_coil_age = 24

        self.last_exit_bar = -999
        self.had_position_last = False

        self.spike_active = False
        self.coiled = False
        self.coil_start_bar = -999

    def _calc_vol(self, closes, period):
        if len(closes) < period + 1:
            return 0.0
        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(-period, 0)]
        mean_ret = sum(log_rets) / period
        variance = sum((r - mean_ret) ** 2 for r in log_rets) / period
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.hist_period + self.vol_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        has_pos = ctx.has_position()
        if self.had_position_last and not has_pos:
            self.last_exit_bar = ctx.bar_index
            self.coiled = False
            self.spike_active = False
        self.had_position_last = has_pos

        if has_pos:
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Avoid extreme crisis / meltdown states
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Sample historical short-term volatilities to build median baseline
        # Stride of 3 bars to keep it fast while maintaining statistical distribution
        vols = []
        for offset in range(0, self.hist_period, 3):
            sub_closes = closes[-(self.vol_period + 1 + offset) : len(closes) - offset]
            vols.append(self._calc_vol(sub_closes, self.vol_period))

        if not vols:
            return None

        sorted_vols = sorted(vols)
        median_vol = sorted_vols[len(sorted_vols) // 2]
        current_vol = vols[0]

        if median_vol <= 1e-6:
            return None

        # Volatility transition state machine: Chaos -> Compression
        if current_vol > 1.8 * median_vol:
            self.spike_active = True
            self.coiled = False

        if self.spike_active and current_vol < 1.05 * median_vol:
            self.spike_active = False
            self.coiled = True
            self.coil_start_bar = ctx.bar_index

        if self.coiled and (ctx.bar_index - self.coil_start_bar > self.max_coil_age):
            self.coiled = False

        if not self.coiled:
            return None

        # Coil release breakout check (using strictly prior bars to prevent lookahead)
        highs = ctx.highs(self.breakout_period + 1)
        lows = ctx.lows(self.breakout_period + 1)
        if len(highs) < self.breakout_period + 1 or len(lows) < self.breakout_period + 1:
            return None

        breakout_high = max(highs[- (self.breakout_period + 1) : -1])
        breakout_low = min(lows[- (self.breakout_period + 1) : -1])
        curr_close = ctx.bar.close

        # Filter out choppy false breaks against market trend regime
        trend_regime = ctx.market.get("trend_regime", "neutral")

        # Prior eth family failure showed false breakdowns in eth bear/neutral chop;
        # Require trend alignment to ensure clean release momentum
        if curr_close > breakout_high and trend_regime != "bear":
            self.coiled = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "coil_expansion_breakout_high",
                    "curr_vol": round(current_vol, 5),
                    "median_vol": round(median_vol, 5),
                    "breakout_high": breakout_high,
                    "trend_regime": trend_regime,
                },
            )

        if curr_close < breakout_low and trend_regime != "bull":
            self.coiled = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "coil_expansion_breakout_low",
                    "curr_vol": round(current_vol, 5),
                    "median_vol": round(median_vol, 5),
                    "breakout_low": breakout_low,
                    "trend_regime": trend_regime,
                },
            )

        return None