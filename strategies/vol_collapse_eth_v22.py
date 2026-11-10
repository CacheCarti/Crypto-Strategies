from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math
import statistics

class VolCollapseCoilBreakout(Strategy):
    METADATA = {
        "name": "VolCollapseCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,  # ~5 hours swing target
        "warmup_bars": 190,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_period = 12
        self.median_period = 168
        self.breakout_period = 12
        self.cooldown_period = 16
        self.max_coil_age = 24
        
        self.state = "IDLE"  # IDLE -> SPIKED -> COILING
        self.coil_start_bar = -1
        self.last_exit_bar = -999

    def _calc_rv(self, closes):
        # Rolling realized volatility: stddev of log returns over rv_period
        if len(closes) < self.rv_period + 1:
            return None
        returns = []
        for i in range(-self.rv_period, 0):
            r = math.log(closes[i] / closes[i - 1])
            returns.append(r)
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        return math.sqrt(var)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.state = "IDLE"

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + self.rv_period + 5)
        highs = ctx.highs(self.breakout_period + 5)
        lows = ctx.lows(self.breakout_period + 5)

        if len(closes) < self.median_period + self.rv_period:
            return None

        # Cooldown guard after position close
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Build rolling RV sequence over median_period to determine median RV
        # Vectorized-style calculation over pre-fetched closes for performance
        rv_series = []
        for offset in range(self.median_period):
            end_idx = len(closes) - offset
            start_idx = end_idx - self.rv_period - 1
            sub_c = closes[start_idx:end_idx]
            
            rets = [math.log(sub_c[i] / sub_c[i-1]) for i in range(1, len(sub_c))]
            m_r = sum(rets) / len(rets)
            var = sum((x - m_r) ** 2 for x in rets) / len(rets)
            rv_series.append(math.sqrt(var))

        current_rv = rv_series[0]
        median_rv = statistics.median(rv_series)

        if median_rv <= 1e-8:
            return None

        # State machine: Chaos (SPIKED) -> Compression (COILING)
        if self.state == "IDLE":
            if current_rv >= 2.0 * median_rv:
                self.state = "SPIKED"
        elif self.state == "SPIKED":
            if current_rv < median_rv:
                self.state = "COILING"
                self.coil_start_bar = ctx.bar_index
        elif self.state == "COILING":
            # Coil expiration: if compression persists too long without release, reset
            if ctx.bar_index - self.coil_start_bar > self.max_coil_age:
                self.state = "IDLE"
            elif current_rv >= 2.0 * median_rv:
                # Re-entered chaos regime directly
                self.state = "SPIKED"

        # Edge gating: Only execute when market regime is not MELTDOWN/CRISIS
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.5:
            return None

        # Only search for entry signals if currently coiling and not in position
        if self.state == "COILING" and not ctx.has_position():
            curr_close = ctx.bar.close
            
            # 12-bar Donchian channel excluding current bar
            prior_high = max(highs[-self.breakout_period - 1:-1])
            prior_low = min(lows[-self.breakout_period - 1:-1])

            trend_regime = ctx.market.get("trend_regime", "neutral")

            # Breakout triggers with trend directional alignment to filter false breaks
            if curr_close > prior_high and trend_regime != "bear":
                self.state = "IDLE"
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_long_breakout",
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "rv_ratio": round(current_rv / median_rv, 2),
                        "prior_high": prior_high,
                        "close": curr_close,
                        "regime": regime,
                    }
                )
            elif curr_close < prior_low and trend_regime != "bull":
                self.state = "IDLE"
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "post_spike_coil_short_breakout",
                        "current_rv": round(current_rv, 6),
                        "median_rv": round(median_rv, 6),
                        "rv_ratio": round(current_rv / median_rv, 2),
                        "prior_low": prior_low,
                        "close": curr_close,
                        "regime": regime,
                    }
                )

        return None