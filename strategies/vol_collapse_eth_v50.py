from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 550.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rv_window = 14
        self.baseline_window = 168
        self.breakout_window = 12
        self.cooldown_bars = 16
        self.max_coil_age = 24

        self.last_exit_bar = -999
        self.spike_active = False
        self.coil_armed = False
        self.coil_start_bar = -999

    def _calc_rv(self, closes, period):
        if len(closes) < period + 1:
            return 0.0
        rets = [abs((closes[i] - closes[i - 1]) / closes[i - 1]) for i in range(-period, 0)]
        return sum(rets) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_armed = False
        self.spike_active = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.baseline_window + self.rv_window + 5)
        if len(closes) < self.baseline_window + self.rv_window + 2:
            return None

        # Cooldown guard after trade exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Manage existing position exits
        if ctx.has_position():
            pos = ctx.position
            entry_p = pos.get("entry_price", ctx.bar.close) if pos else ctx.bar.close
            direction = ctx.position_direction()
            
            # Simple time-based / reversal exit logic
            if direction == "long" and ctx.bar.close < min(ctx.lows(4)[:-1]):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "long_trailing_low_exit", "price": ctx.bar.close})
            elif direction == "short" and ctx.bar.close > max(ctx.highs(4)[:-1]):
                return ctx.signal("flat", confidence=0.6, metadata={"reason": "short_trailing_high_exit", "price": ctx.bar.close})
            return None

        # Calculate current realized volatility
        current_rv = self._calc_rv(closes, self.rv_window)

        # Calculate baseline median RV over the last baseline_window bars
        rv_hist = []
        for offset in range(self.baseline_window):
            end_idx = len(closes) - offset
            start_idx = end_idx - (self.rv_window + 1)
            sub_closes = closes[start_idx:end_idx]
            rets = [abs((sub_closes[i] - sub_closes[i - 1]) / sub_closes[i - 1]) for i in range(1, len(sub_closes))]
            rv_hist.append(sum(rets) / len(rets))

        rv_hist.sort()
        median_rv = rv_hist[len(rv_hist) // 2]
        if median_rv <= 0:
            return None

        # Regime state machine: Spike (Chaos) -> Collapse (Compression/Coil) -> Breakout Trigger
        if current_rv >= 1.85 * median_rv:
            self.spike_active = True
            self.coil_armed = False

        if self.spike_active and current_rv <= 1.05 * median_rv:
            self.spike_active = False
            self.coil_armed = True
            self.coil_start_bar = ctx.bar_index

        # Expire coil if no breakout happens within allowable window
        if self.coil_armed and (ctx.bar_index - self.coil_start_bar > self.max_coil_age):
            self.coil_armed = False

        if not self.coil_armed:
            return None

        # Breakout Trigger: Strict break of the prior 12-bar Donchian channel
        highs = ctx.highs(self.breakout_window + 1)
        lows = ctx.lows(self.breakout_window + 1)
        if len(highs) < self.breakout_window + 1 or len(lows) < self.breakout_window + 1:
            return None

        prior_high = max(highs[:-1])
        prior_low = min(lows[:-1])
        current_close = ctx.bar.close

        # Prevent trading during extreme crisis meltdowns
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.8:
            return None

        if current_close > prior_high:
            self.coil_armed = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_upside_release",
                    "rv_ratio": round(current_rv / median_rv, 3),
                    "prior_high": prior_high,
                    "close": current_close,
                },
            )

        if current_close < prior_low:
            self.coil_armed = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_downside_release",
                    "rv_ratio": round(current_rv / median_rv, 3),
                    "prior_low": prior_low,
                    "close": current_close,
                },
            )

        return None