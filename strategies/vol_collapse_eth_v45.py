from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 28800,  # 8 hours typical swing
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_lookback = 168
        self.channel_len = 12
        self.cooldown_bars = 16
        self.last_exit_bar = -999
        self.spike_detected = False
        self.armed_for_breakout = False
        self.armed_bar = -999
        self.arm_expiry = 24  # coil release window

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.armed_for_breakout = False
        self.spike_detected = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_req = self.vol_lookback + 20
        closes = ctx.closes(total_req)
        highs = ctx.highs(total_req)
        lows = ctx.lows(total_req)

        if len(closes) < total_req:
            return None

        # Mandatory cooldown after any exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # If already holding a position, manage exit via trailing or SL/TP handled by engine
        if ctx.has_position():
            return None

        # Compute rolling 1-bar true ranges for volatility baseline
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)

        # Current short-term realized volatility (3-bar average TR)
        short_vol = sum(trs[-3:]) / 3.0
        # Historical baseline: 168-bar median true range
        baseline_trs = sorted(trs[-self.vol_lookback:])
        median_vol = baseline_trs[len(baseline_trs) // 2]

        if median_vol <= 0:
            return None

        # Transition logic: chaos -> compression
        # 1. State: Spike detected if short_vol >= 2.0x median
        if short_vol >= 2.0 * median_vol:
            self.spike_detected = True

        # 2. State: Compression confirmed if after spike, short_vol drops back <= median
        if self.spike_detected and short_vol <= 1.05 * median_vol:
            self.spike_detected = False
            self.armed_for_breakout = True
            self.armed_bar = ctx.bar_index

        # Expire armed state if coil doesn't release within window
        if self.armed_for_breakout and (ctx.bar_index - self.armed_bar > self.arm_expiry):
            self.armed_for_breakout = False

        if not self.armed_for_breakout:
            return None

        # Channel breakout levels: prior 12 bars (excluding current bar)
        prior_highs = highs[-self.channel_len - 1:-1]
        prior_lows = lows[-self.channel_len - 1:-1]
        breakout_high = max(prior_highs)
        breakout_low = min(prior_lows)

        current_close = closes[-1]
        current_open = ctx.bar.open

        # Regime context: avoid taking entries during acute meltdown or crisis
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.65:
            return None

        # Dynamic stop calculation based on ATR
        atr = self._atr(highs[-20:], lows[-20:], closes[-20:], period=14)
        if atr is None or atr == 0:
            return None

        stop_bps = max(180.0, min(380.0, (atr * 1.8 / current_close) * 10000.0))
        tp_bps = stop_bps * 2.2

        # Breakout trigger: decisive breakout of channel
        if current_close > breakout_high and current_close > current_open:
            self.armed_for_breakout = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=28800,
                metadata={
                    "reason": "coil_release_bullish_breakout",
                    "breakout_high": breakout_high,
                    "close": current_close,
                    "short_vol": short_vol,
                    "median_vol": median_vol,
                    "crisis_score": crisis_score,
                }
            )

        if current_close < breakout_low and current_close < current_open:
            self.armed_for_breakout = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=stop_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=28800,
                metadata={
                    "reason": "coil_release_bearish_breakdown",
                    "breakout_low": breakout_low,
                    "close": current_close,
                    "short_vol": short_vol,
                    "median_vol": median_vol,
                    "crisis_score": crisis_score,
                }
            )

        return None