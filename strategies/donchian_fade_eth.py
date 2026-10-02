from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "FailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 440.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.atr_period = 14
        self.rsi_period = 14
        self.vol_period = 20
        self.min_wick_atr_mult = 0.65
        self.cooldown_bars = 10
        self.max_hold_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
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
        req_bars = max(self.lookback + 2, self.atr_period + 2, self.rsi_period + 2, self.vol_period + 2)
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)
        volumes = ctx.volumes(req_bars)

        if len(closes) < req_bars or len(highs) < req_bars or len(lows) < req_bars or len(volumes) < req_bars:
            return None

        # Position time-stop management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_held_reached", "bars_held": bars_held, "price": ctx.bar.close},
                )
            return None

        # Hard cooldown after every position exit
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.rsi_period)
        if atr is None or rsi is None or atr <= 0.0:
            return None

        # Volume filter: ensure meaningful liquidity during fake breakout
        avg_vol = sum(volumes[-(self.vol_period + 1):-1]) / self.vol_period
        vol_ratio = ctx.bar.volume / avg_vol if avg_vol > 0 else 1.0
        if vol_ratio < 1.05:
            return None

        # Market regime filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_close = ctx.bar.close
        candle_range = max(curr_high - curr_low, 1e-6)

        min_wick = atr * self.min_wick_atr_mult

        # 1. Bearish Rejection (Bull Trap Fade -> Short)
        # Deep pierce above range high + close back well inside range in bottom 45% of bar
        upper_wick = curr_high - max(ctx.bar.open, curr_close)
        is_bull_trap = (
            curr_high >= range_high + min_wick
            and curr_close < range_high
            and (curr_close - curr_low) / candle_range <= 0.45
            and upper_wick >= 0.40 * candle_range
            and 52.0 <= rsi <= 76.0
        )

        if is_bull_trap:
            wick_bps = ((curr_high - curr_close) / curr_close) * 10000.0
            sl_bps = min(max(wick_bps + 40.0, 160.0), 260.0)
            tp_bps = 440.0

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.78,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_trap_pinbar_rejection",
                    "range_high": range_high,
                    "wick_high": curr_high,
                    "wick_bps": wick_bps,
                    "atr": atr,
                    "rsi": rsi,
                    "vol_ratio": vol_ratio,
                    "price": curr_close,
                },
            )

        # 2. Bullish Rejection (Bear Trap Fade -> Long)
        # Deep pierce below range low + close back well inside range in top 45% of bar
        lower_wick = min(ctx.bar.open, curr_close) - curr_low
        is_bear_trap = (
            curr_low <= range_low - min_wick
            and curr_close > range_low
            and (curr_close - curr_low) / candle_range >= 0.55
            and lower_wick >= 0.40 * candle_range
            and 24.0 <= rsi <= 48.0
        )

        if is_bear_trap:
            wick_bps = ((curr_close - curr_low) / curr_close) * 10000.0
            sl_bps = min(max(wick_bps + 40.0, 160.0), 260.0)
            tp_bps = 440.0

            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.78,
                stop_loss_bps=sl_bps,
                take_profit_bps=tp_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_trap_pinbar_rejection",
                    "range_low": range_low,
                    "wick_low": curr_low,
                    "wick_bps": wick_bps,
                    "atr": atr,
                    "rsi": rsi,
                    "vol_ratio": vol_ratio,
                    "price": curr_close,
                },
            )

        return None