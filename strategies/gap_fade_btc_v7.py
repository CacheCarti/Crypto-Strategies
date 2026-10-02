from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcWickReversion(Strategy):
    METADATA = {
        "name": "BtcWickReversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 420.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 35,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.period = 24
        self.rsi_period = 14
        self.wick_ratio_threshold = 0.65
        self.vol_mult = 1.35
        self.range_mult = 1.30
        self.cooldown_bars = 10
        self.max_hold_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = None

    def _sma(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.period + 10)
        highs = ctx.highs(self.period + 10)
        lows = ctx.lows(self.period + 10)
        volumes = ctx.volumes(self.period + 10)

        if len(closes) < self.period + 5:
            return None

        # Position management: time-based exit
        if ctx.has_position():
            if self.entry_bar is not None and (ctx.bar_index - self.entry_bar >= self.max_hold_bars):
                self.last_exit_bar = ctx.bar_index
                self.entry_bar = None
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_based_wick_reversion_exit", "bars_held": self.max_hold_bars, "price": ctx.bar.close}
                )
            return None

        # Hard cooldown guard to strictly manage trade frequency and eliminate friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid choppy/panic conditions
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.45 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate indicators
        atr = self._atr(highs, lows, closes, self.rsi_period)
        avg_vol = self._sma(volumes, self.period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or avg_vol is None or rsi is None or atr <= 0.0 or avg_vol <= 0.0:
            return None

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_open = ctx.bar.open
        curr_close = ctx.bar.close
        curr_vol = ctx.bar.volume

        bar_range = curr_high - curr_low
        if bar_range <= 0.0:
            return None

        body_top = max(curr_open, curr_close)
        body_bottom = min(curr_open, curr_close)
        upper_wick = curr_high - body_top
        lower_wick = body_bottom - curr_low

        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range
        range_ratio = bar_range / atr
        vol_ratio = curr_vol / avg_vol

        # Stricter volatility and volume surge filters to ensure high-conviction rejection bars
        if range_ratio < self.range_mult or vol_ratio < self.vol_mult:
            return None

        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)

        # Long Entry: Strong lower wick rejection (>65% of range), neutral/discounted RSI, avoid extreme long crowding
        if lower_wick_ratio >= self.wick_ratio_threshold and rsi <= 52.0 and funding_rate <= 0.0003:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + 0.3 * (lower_wick_ratio - self.wick_ratio_threshold) + 0.1 * min(vol_ratio - 1.0, 1.0))
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "lower_wick_rejection_long",
                    "lower_wick_ratio": round(lower_wick_ratio, 3),
                    "range_ratio": round(range_ratio, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "rsi": round(rsi, 2),
                    "funding_rate": funding_rate,
                    "price": curr_close,
                }
            )

        # Short Entry: Strong upper wick rejection (>65% of range), elevated RSI, avoid extreme short crowding
        if upper_wick_ratio >= self.wick_ratio_threshold and rsi >= 48.0 and funding_rate >= -0.0003:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + 0.3 * (upper_wick_ratio - self.wick_ratio_threshold) + 0.1 * min(vol_ratio - 1.0, 1.0))
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "upper_wick_rejection_short",
                    "upper_wick_ratio": round(upper_wick_ratio, 3),
                    "range_ratio": round(range_ratio, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "rsi": round(rsi, 2),
                    "funding_rate": funding_rate,
                    "price": curr_close,
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = None