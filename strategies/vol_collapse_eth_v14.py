from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 14
        self.baseline_period = 168
        self.channel_period = 12
        self.spike_threshold = 1.8
        self.cooldown_period = 10
        self.spike_detected = False
        self.coiled = False
        self.bars_in_coil = 0
        self.max_coil_bars = 36
        self.last_exit_bar = -100

    def _atr(self, highs, lows, closes, period: int):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.coiled = False
        self.spike_detected = False
        self.bars_in_coil = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.baseline_period + self.vol_period + 5
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)

        # 1. Realized volatility tracking via short ATR series
        current_atr = self._atr(highs, lows, closes, self.vol_period)
        if current_atr is None:
            return None

        # Build rolling ATR series over baseline window to calculate median
        atr_history = []
        step = 4  # sample every 4 bars for fast calculation
        for idx in range(total_needed - self.baseline_period, total_needed + 1, step):
            sub_c = closes[:idx]
            sub_h = highs[:idx]
            sub_l = lows[:idx]
            val = self._atr(sub_h, sub_l, sub_c, self.vol_period)
            if val is not None:
                atr_history.append(val)

        if len(atr_history) < 10:
            return None

        sorted_atr = sorted(atr_history)
        median_atr = sorted_atr[len(sorted_atr) // 2]
        if median_atr <= 0:
            return None

        vol_ratio = current_atr / median_atr

        # 2. State machine for Spike -> Coil Compression
        if vol_ratio >= self.spike_threshold:
            self.spike_detected = True
            self.coiled = False
            self.bars_in_coil = 0

        elif self.spike_detected and vol_ratio < 1.05:
            # Transitioned from chaos into tight compression (coil)
            self.coiled = True
            self.spike_detected = False
            self.bars_in_coil = 0

        if self.coiled:
            self.bars_in_coil += 1
            if self.bars_in_coil > self.max_coil_bars:
                self.coiled = False
                self.bars_in_coil = 0

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # 3. Position management: exit if coiled release fizzles
        if ctx.has_position():
            pos = ctx.position
            if pos and "entry_price" in pos:
                entry = pos["entry_price"]
                curr = ctx.bar.close
                # If adverse move after 15 bars, cut early
                if ctx.position_direction() == "long" and curr < entry * 0.985:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "long_coil_breakout_fail", "entry": entry, "curr": curr})
                elif ctx.position_direction() == "short" and curr > entry * 1.015:
                    return ctx.signal("flat", confidence=0.5, metadata={"reason": "short_coil_breakout_fail", "entry": entry, "curr": curr})
            return None

        # 4. Entry signals when in coiled regime
        if not self.coiled:
            return None

        # Donchian channel over previous channel_period bars (excluding current bar)
        prev_highs = highs[-self.channel_period - 1:-1]
        prev_lows = lows[-self.channel_period - 1:-1]
        if len(prev_highs) < self.channel_period:
            return None

        channel_high = max(prev_highs)
        channel_low = min(prev_lows)
        curr_close = ctx.bar.close

        # Volatility regime filter: Avoid crisis meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # BTC cross-asset trend confirmation
        btc_return = ctx.market.get("btc_return_pct", 0.0)

        # Long breakout: close breaks above 12-bar high and BTC not plunging
        if curr_close > channel_high and btc_return > -1.5:
            self.coiled = False
            return ctx.signal(
                "long",
                confidence=0.80,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "coil_release_high_breakout",
                    "channel_high": channel_high,
                    "close": curr_close,
                    "vol_ratio": round(vol_ratio, 2),
                    "btc_return": round(btc_return, 2),
                    "bars_in_coil": self.bars_in_coil
                }
            )

        # Short breakdown: close breaks below 12-bar low and BTC not pumping
        if curr_close < channel_low and btc_return < 1.5:
            self.coiled = False
            return ctx.signal(
                "short",
                confidence=0.80,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "coil_release_low_breakdown",
                    "channel_low": channel_low,
                    "close": curr_close,
                    "vol_ratio": round(vol_ratio, 2),
                    "btc_return": round(btc_return, 2),
                    "bars_in_coil": self.bars_in_coil
                }
            )

        return None