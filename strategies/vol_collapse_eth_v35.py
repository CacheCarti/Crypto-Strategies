from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class PostSpikeCoilBreakout(Strategy):
    METADATA = {
        "name": "PostSpikeCoilBreakout",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 180,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.med_window = 168
        self.channel_period = 12
        self.cooldown_period = 8
        self.last_exit_bar = -999
        self.spike_active = False
        self.compression_armed = False
        self.armed_bar = -999

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
        self.compression_armed = False

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.med_window + 15
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)

        if ctx.bar_index - self.last_exit_bar < self.cooldown_period:
            return None

        # Compute rolling 14-period ATR values to assess volatility
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])) for i in range(1, len(closes))]
        if len(trs) < self.med_window + 14:
            return None

        atrs = []
        for i in range(14, len(trs) + 1):
            atrs.append(sum(trs[i - 14:i]) / 14.0)

        if len(atrs) < self.med_window:
            return None

        window_atrs = atrs[-self.med_window:]
        sorted_atrs = sorted(window_atrs)
        median_atr = sorted_atrs[len(sorted_atrs) // 2]
        current_atr = atrs[-1]

        # Track chaotic spike transition to compression
        if current_atr > 1.8 * median_atr:
            self.spike_active = True
        elif self.spike_active and current_atr <= median_atr:
            self.spike_active = False
            self.compression_armed = True
            self.armed_bar = ctx.bar_index

        # Expire armed setup if no coil breakout happens within 24 bars
        if self.compression_armed and (ctx.bar_index - self.armed_bar > 24):
            self.compression_armed = False

        if ctx.has_position():
            return None

        if not self.compression_armed:
            return None

        # 12-bar Donchian breakout on coil release (excluding current bar)
        prior_highs = highs[-(self.channel_period + 1):-1]
        prior_lows = lows[-(self.channel_period + 1):-1]
        highest_prev = max(prior_highs)
        lowest_prev = min(prior_lows)
        current_close = closes[-1]

        # Trend and market regime sanity check
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        sma50 = sum(closes[-50:]) / 50.0

        if current_close > highest_prev and current_close > sma50:
            self.compression_armed = False
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_upper_breakout",
                    "current_atr": current_atr,
                    "median_atr": median_atr,
                    "breakout_level": highest_prev,
                    "sma50": sma50,
                }
            )
        elif current_close < lowest_prev and current_close < sma50:
            self.compression_armed = False
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "post_spike_coil_lower_breakout",
                    "current_atr": current_atr,
                    "median_atr": median_atr,
                    "breakout_level": lowest_prev,
                    "sma50": sma50,
                }
            )

        return None