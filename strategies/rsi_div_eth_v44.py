from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceRegime(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceRegime",
        "domain": "eth_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.pivot_window = 4
        self.max_div_bars = 28
        self.cooldown_bars = 10
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _calc_rsi_series(self, closes: list, period: int = 14) -> list:
        if len(closes) < period + 1:
            return []
        rsis = []
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        if len(gains) < period:
            return []

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0.0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100.0 - (100.0 / (1.0 + rs)))

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0.0:
                rsis.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsis.append(100.0 - (100.0 / (1.0 + rs)))

        return rsis

    def _find_recent_troughs(self, lows: list, rsis: list, offset: int, lookback: int):
        troughs = []
        w = self.pivot_window
        total = len(lows)
        start_idx = max(w, total - lookback)
        end_idx = total - w
        for i in range(end_idx - 1, start_idx - 1, -1):
            if all(lows[i] <= lows[i - k] for k in range(1, w + 1)) and all(lows[i] <= lows[i + k] for k in range(1, w + 1)):
                rsi_idx = i - offset
                if 0 <= rsi_idx < len(rsis):
                    troughs.append((i, lows[i], rsis[rsi_idx]))
                if len(troughs) == 2:
                    break
        return troughs

    def _find_recent_peaks(self, highs: list, rsis: list, offset: int, lookback: int):
        peaks = []
        w = self.pivot_window
        total = len(highs)
        start_idx = max(w, total - lookback)
        end_idx = total - w
        for i in range(end_idx - 1, start_idx - 1, -1):
            if all(highs[i] >= highs[i - k] for k in range(1, w + 1)) and all(highs[i] >= highs[i + k] for k in range(1, w + 1)):
                rsi_idx = i - offset
                if 0 <= rsi_idx < len(rsis):
                    peaks.append((i, highs[i], rsis[rsi_idx]))
                if len(peaks) == 2:
                    break
        return peaks

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_needed = self.max_div_bars + self.rsi_period + self.pivot_window + 10
        closes = ctx.closes(total_needed)
        if len(closes) < total_needed:
            return None

        highs = ctx.highs(total_needed)
        lows = ctx.lows(total_needed)
        rsis = self._calc_rsi_series(closes, self.rsi_period)
        if not rsis:
            return None

        current_rsi = rsis[-1]
        offset = len(closes) - len(rsis)

        # In-position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.last_entry_bar
            if pos_dir == "long" and (current_rsi >= 62.0 or bars_held >= 12):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_long", "rsi": round(current_rsi, 2), "bars_held": bars_held})
            elif pos_dir == "short" and (current_rsi <= 38.0 or bars_held >= 12):
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "rsi_reversion_exit_short", "rsi": round(current_rsi, 2), "bars_held": bars_held})
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Bullish Divergence Search: Price lower low, RSI higher low
        troughs = self._find_recent_troughs(lows, rsis, offset, self.max_div_bars)
        if len(troughs) == 2:
            p2_bar, p2_low, p2_rsi = troughs[0]
            p1_bar, p1_low, p1_rsi = troughs[1]
            dist = p2_bar - p1_bar
            bar_lag = (len(closes) - 1) - p2_bar
            if 5 <= dist <= self.max_div_bars and bar_lag <= self.pivot_window + 2:
                if p2_low < p1_low and p2_rsi > p1_rsi + 2.0 and p2_rsi < 44.0:
                    if btc_ret > -3.0:
                        self.last_entry_bar = ctx.bar_index
                        return ctx.signal(
                            "long",
                            confidence=0.78,
                            stop_loss_bps=320.0,
                            take_profit_bps=520.0,
                            horizon_seconds=18000,
                            metadata={
                                "reason": "bullish_rsi_divergence",
                                "p1_rsi": round(p1_rsi, 2),
                                "p2_rsi": round(p2_rsi, 2),
                                "p1_low": round(p1_low, 2),
                                "p2_low": round(p2_low, 2),
                                "btc_ret": round(btc_ret, 2),
                            },
                        )

        # Bearish Divergence Search: Price higher high, RSI lower high
        peaks = self._find_recent_peaks(highs, rsis, offset, self.max_div_bars)
        if len(peaks) == 2:
            p2_bar, p2_high, p2_rsi = peaks[0]
            p1_bar, p1_high, p1_rsi = peaks[1]
            dist = p2_bar - p1_bar
            bar_lag = (len(closes) - 1) - p2_bar
            if 5 <= dist <= self.max_div_bars and bar_lag <= self.pivot_window + 2:
                if p2_high > p1_high and p2_rsi < p1_rsi - 2.0 and p2_rsi > 56.0:
                    if btc_ret < 3.0:
                        self.last_entry_bar = ctx.bar_index
                        return ctx.signal(
                            "short",
                            confidence=0.78,
                            stop_loss_bps=320.0,
                            take_profit_bps=520.0,
                            horizon_seconds=18000,
                            metadata={
                                "reason": "bearish_rsi_divergence",
                                "p1_rsi": round(p1_rsi, 2),
                                "p2_rsi": round(p2_rsi, 2),
                                "p1_high": round(p1_high, 2),
                                "p2_high": round(p2_high, 2),
                                "btc_ret": round(btc_ret, 2),
                            },
                        )

        return None