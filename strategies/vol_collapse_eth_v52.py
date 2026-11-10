from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilRelease(Strategy):
    METADATA = {
        "name": "PostSpikeCoilRelease",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 168
        self.breakout_period = 12
        self.cooldown_period = 14
        self.spike_memory_bars = 24
        
        self.last_exit_bar = -999
        self.last_spike_bar = -999
        self.coiled = False

    def _realized_vol(self, closes, window=10):
        if len(closes) < window + 1:
            return None
        returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(-window, 0)]
        mean_ret = sum(returns) / window
        variance = sum((r - mean_ret) ** 2 for r in returns) / window
        return math.sqrt(variance)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        highs = ctx.highs(self.breakout_period + 2)
        lows = ctx.lows(self.breakout_period + 2)

        if len(closes) < self.lookback + 15:
            return None

        # Calculate short-window realized volatility for each bar across the lookback window
        window = 10
        vols = []
        for i in range(self.lookback):
            idx_end = len(closes) - self.lookback + i + 1
            idx_start = idx_end - window - 1
            if idx_start < 0:
                continue
            sub_closes = closes[idx_start:idx_end]
            rets = [(sub_closes[j] - sub_closes[j - 1]) / sub_closes[j - 1] for j in range(1, len(sub_closes))]
            m = sum(rets) / len(rets)
            var = sum((r - m) ** 2 for r in rets) / len(rets)
            vols.append(math.sqrt(var))

        if len(vols) < 50:
            return None

        current_vol = vols[-1]
        sorted_vols = sorted(vols)
        median_vol = sorted_vols[len(sorted_vols) // 2]
        if median_vol <= 0:
            return None

        # Track volatility spike (> 2x median) and subsequent collapse back below median
        if current_vol > 2.0 * median_vol:
            self.last_spike_bar = ctx.bar_index
            self.coiled = False

        if (
            not self.coiled
            and (ctx.bar_index - self.last_spike_bar) <= self.spike_memory_bars
            and current_vol <= median_vol
        ):
            self.coiled = True

        if ctx.has_position():
            return None

        # Cooldown guard to avoid churn and friction drag
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Regime Gate: Prior evidence shows false breakouts in high-crisis/meltdown regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.45:
            return None

        # Coil breakout logic
        if self.coiled:
            recent_high = max(highs[-(self.breakout_period + 1):-1])
            recent_low = min(lows[-(self.breakout_period + 1):-1])
            price = ctx.bar.close

            if price > recent_high:
                self.coiled = False
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "post_spike_coil_upper_breakout",
                        "price": price,
                        "recent_high": recent_high,
                        "current_vol": round(current_vol, 5),
                        "median_vol": round(median_vol, 5),
                        "crisis_score": round(crisis_score, 2),
                    },
                )
            elif price < recent_low:
                self.coiled = False
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=220.0,
                    take_profit_bps=440.0,
                    horizon_seconds=14400,
                    metadata={
                        "reason": "post_spike_coil_lower_breakout",
                        "price": price,
                        "recent_low": recent_low,
                        "current_vol": round(current_vol, 5),
                        "median_vol": round(median_vol, 5),
                        "crisis_score": round(crisis_score, 2),
                    },
                )

        return None