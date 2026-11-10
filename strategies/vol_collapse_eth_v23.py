from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 21600,  # ~6 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 14
        self.median_period = 168
        self.donchian_period = 12
        self.cooldown_bars = 10
        self.last_exit_bar = -100

        self.spike_detected = False
        self.coil_ready = False
        self.cooldown_timer = 0

    def _stddev(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        sub = values[-period:]
        mean = sum(sub) / period
        variance = sum((x - mean) ** 2 for x in sub) / period
        return math.sqrt(variance)

    def _median(self, values: list) -> Optional[float]:
        if not values:
            return None
        s = sorted(values)
        n = len(s)
        mid = n // 2
        if n % 2 == 1:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2.0

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coil_ready = False
        self.spike_detected = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.median_period + self.vol_period + 5)
        highs = ctx.highs(self.donchian_period + 5)
        lows = ctx.lows(self.donchian_period + 5)

        if len(closes) < self.median_period + self.vol_period:
            return None

        # Cooldown guard after trade exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        if ctx.has_position():
            return None

        # 1. Compute rolling realized volatility (14-bar stdev of relative price changes)
        returns = []
        for i in range(1, len(closes)):
            prev = closes[i - 1]
            if prev > 0:
                returns.append((closes[i] - prev) / prev)
            else:
                returns.append(0.0)

        if len(returns) < self.median_period + self.vol_period:
            return None

        # Build rolling volatility estimates for median baseline
        hist_vols = []
        start_idx = len(returns) - self.median_period
        for idx in range(start_idx, len(returns) + 1):
            window = returns[idx - self.vol_period:idx]
            m = sum(window) / self.vol_period
            var = sum((x - m) ** 2 for x in window) / self.vol_period
            hist_vols.append(math.sqrt(var))

        current_vol = hist_vols[-1]
        baseline_vols = hist_vols[:-1]
        med_vol = self._median(baseline_vols)

        if med_vol is None or med_vol <= 0:
            return None

        # 2. State Machine: Volatility Spike (>1.8x median) followed by collapse (<= 1.05x median)
        if current_vol > 1.8 * med_vol:
            self.spike_detected = True
            self.coil_ready = False

        if self.spike_detected and current_vol <= 1.05 * med_vol:
            self.coil_ready = True
            self.spike_detected = False

        # 3. If coil compression is ready, check for breakout of 12-bar Donchian channel
        if self.coil_ready and len(highs) >= self.donchian_period + 1:
            recent_high = max(highs[-(self.donchian_period + 1):-1])
            recent_low = min(lows[-(self.donchian_period + 1):-1])
            curr_close = ctx.bar.close

            # Trend & Crisis regime alignment
            trend = ctx.market.get("trend_regime", "neutral")
            crisis_score = ctx.market.get("crisis_score", 0.0)

            if crisis_score > 0.65:
                # In active meltdown, abort coil setup
                self.coil_ready = False
                return None

            # Bullish coil release
            if curr_close > recent_high and trend != "bear":
                self.coil_ready = False
                return ctx.signal(
                    "long",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "coil_breakout_long",
                        "current_vol": round(current_vol, 5),
                        "median_vol": round(med_vol, 5),
                        "breakout_level": round(recent_high, 2),
                        "close": round(curr_close, 2),
                    }
                )

            # Bearish coil release
            if curr_close < recent_low and trend != "bull":
                self.coil_ready = False
                return ctx.signal(
                    "short",
                    confidence=0.75,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "coil_breakout_short",
                        "current_vol": round(current_vol, 5),
                        "median_vol": round(med_vol, 5),
                        "breakout_level": round(recent_low, 2),
                        "close": round(curr_close, 2),
                    }
                )

        return None