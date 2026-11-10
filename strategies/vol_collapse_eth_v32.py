from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,  # ~5 hours target swing hold
        "warmup_bars": 185,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.median_period = 168
        self.breakout_period = 12
        self.cooldown_bars = 10
        self.max_coiled_age = 24
        
        self.spiked = False
        self.coiled = False
        self.coiled_bar = -1
        self.last_exit_bar = -100

    def _median(self, values: list) -> float:
        s = sorted(values)
        n = len(s)
        mid = n // 2
        return s[mid] if n % 2 != 0 else (s[mid - 1] + s[mid]) / 2.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False
        self.spiked = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + 20)
        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)
        
        if len(closes) < self.median_period + 15 or len(highs) < self.breakout_period + 1:
            return None

        # 1. Realized volatility estimate: 14-bar rolling log-return standard deviation
        log_rets = []
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            curr = closes[i]
            if prev > 0 and curr > 0:
                log_rets.append(math.log(curr / prev))
            else:
                log_rets.append(0.0)

        rv_window = 14
        if len(log_rets) < rv_window + self.median_period:
            return None

        recent_rvs = []
        for idx in range(len(log_rets) - self.median_period, len(log_rets) + 1):
            sub = log_rets[idx - rv_window:idx]
            m = sum(sub) / rv_window
            var = sum((x - m) ** 2 for x in sub) / rv_window
            recent_rvs.append(math.sqrt(var))

        current_rv = recent_rvs[-1]
        baseline_median_rv = self._median(recent_rvs[:-1])

        # State machine: chaos (spike > 2.0x median) -> compression (collapse back <= 1.0x median)
        if current_rv > 2.0 * baseline_median_rv:
            self.spiked = True

        if self.spiked and current_rv <= baseline_median_rv:
            self.spiked = False
            self.coiled = True
            self.coiled_bar = ctx.bar_index

        # Expire coil if it remains dormant too long
        if self.coiled and (ctx.bar_index - self.coiled_bar > self.max_coiled_age):
            self.coiled = False

        # Regime gate to fix prior OOS failure: avoid entering in Meltdown/Crisis
        market_regime = ctx.market.get("regime", "NORMAL").upper()
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={"reason": "regime_risk_exit", "regime": market_regime}
                )
            return None

        # Manage open position via coil expansion take-profit / reversion
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            # Dynamic exit: if volatility expands to 2.5x median, harvest coil breakout
            if current_rv >= 2.5 * baseline_median_rv:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "volatility_expansion_harvest",
                        "current_rv": current_rv,
                        "median_rv": baseline_median_rv
                    }
                )
            return None

        # Gating entries: cooldown and armed state
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        if not self.coiled:
            return None

        # 12-bar Donchian breakout excluding current bar
        prior_high = max(highs[-self.breakout_period - 1:-1])
        prior_low = min(lows[-self.breakout_period - 1:-1])
        current_close = ctx.bar.close

        # Trend regime alignment to ensure edge consistency
        trend = ctx.market.get("trend_regime", "neutral").lower()

        if current_close > prior_high and trend != "bear":
            self.coiled = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=450.0,
                metadata={
                    "reason": "coil_release_upside_breakout",
                    "breakout_level": prior_high,
                    "close": current_close,
                    "current_rv": current_rv,
                    "median_rv": baseline_median_rv,
                    "trend": trend
                }
            )

        if current_close < prior_low and trend != "bull":
            self.coiled = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=250.0,
                take_profit_bps=450.0,
                metadata={
                    "reason": "coil_release_downside_breakout",
                    "breakout_level": prior_low,
                    "close": current_close,
                    "current_rv": current_rv,
                    "median_rv": baseline_median_rv,
                    "trend": trend
                }
            )

        return None