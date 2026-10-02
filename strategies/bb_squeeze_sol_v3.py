from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math


class SolBollingerSqueezeBreakout(Strategy):
    METADATA = {
        "name": "SOL Bollinger Squeeze Breakout",
        "domain": "sol_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 120,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.bb_period = 20
        self.bb_std = 2.0
        self.squeeze_lookback = 100
        self.squeeze_percentile = 0.15
        self.cooldown_bars = 20
        self.last_exit_bar = -999

    def _sma(self, values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _bollinger(self, closes: List[float], period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None, None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        upper = mean + num_std * std
        lower = mean - num_std * std
        bandwidth = (upper - lower) / mean if mean > 0 else 0.0
        return mean, upper, lower, bandwidth

    def _rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        # Filter out extreme crisis regimes
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        total_history_needed = self.squeeze_lookback + self.bb_period + 5
        closes = ctx.closes(total_history_needed)
        opens = ctx.opens(total_history_needed)
        if len(closes) < total_history_needed:
            return None

        current_close = closes[-1]
        prev_close = closes[-2]
        current_open = opens[-1]

        mid, upper, lower, current_bw = self._bollinger(closes, self.bb_period, self.bb_std)
        prev_mid, prev_upper, prev_lower, _ = self._bollinger(closes[:-1], self.bb_period, self.bb_std)
        rsi = self._rsi(closes, 14)

        if None in (mid, upper, lower, prev_upper, prev_lower, rsi):
            return None

        # Position Management & Exits
        if ctx.has_position():
            direction = ctx.position_direction()

            if direction == "long":
                # Exit when momentum clearly breaks below the median band with bearish RSI
                if current_close < mid and rsi < 45.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "long_momentum_break_below_mid",
                            "close": current_close,
                            "bb_mid": mid,
                            "rsi": rsi,
                        },
                    )
            elif direction == "short":
                # Exit when momentum clearly breaks above the median band with bullish RSI
                if current_close > mid and rsi > 55.0:
                    self.last_exit_bar = ctx.bar_index
                    return ctx.signal(
                        "flat",
                        confidence=0.8,
                        metadata={
                            "reason": "short_momentum_break_above_mid",
                            "close": current_close,
                            "bb_mid": mid,
                            "rsi": rsi,
                        },
                    )
            return None

        # Enforce strict multi-bar cooldown after position exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Compute historical bandwidth percentiles over the lookback window
        bandwidths = []
        for i in range(self.squeeze_lookback):
            idx_end = len(closes) - (self.squeeze_lookback - 1 - i)
            sub_closes = closes[:idx_end]
            _, _, _, bw = self._bollinger(sub_closes, self.bb_period, self.bb_std)
            if bw is not None:
                bandwidths.append(bw)

        if len(bandwidths) < self.squeeze_lookback:
            return None

        sorted_bws = sorted(bandwidths)
        pct_idx = int(len(sorted_bws) * self.squeeze_percentile)
        threshold_bw = sorted_bws[min(pct_idx, len(sorted_bws) - 1)]

        # Ensure a genuine squeeze occurred in the recent past (within 2 to 6 bars ago)
        recent_squeeze = any(bw <= threshold_bw for bw in bandwidths[-6:-1])
        if not recent_squeeze:
            return None

        # Fresh bullish breakout: previous close was inside bands, current close decisively breaks above
        bullish_breakout = (prev_close <= prev_upper) and (current_close > upper) and (current_close > current_open)
        if bullish_breakout and rsi > 58.0:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bb_squeeze_bullish_breakout",
                    "close": current_close,
                    "bb_upper": upper,
                    "bandwidth": current_bw,
                    "threshold_bandwidth": threshold_bw,
                    "rsi": rsi,
                },
            )

        # Fresh bearish breakdown: previous close was inside bands, current close decisively breaks below
        bearish_breakdown = (prev_close >= prev_lower) and (current_close < lower) and (current_close < current_open)
        if bearish_breakdown and rsi < 42.0:
            self.last_exit_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bb_squeeze_bearish_breakdown",
                    "close": current_close,
                    "bb_lower": lower,
                    "bandwidth": current_bw,
                    "threshold_bandwidth": threshold_bw,
                    "rsi": rsi,
                },
            )

        return None