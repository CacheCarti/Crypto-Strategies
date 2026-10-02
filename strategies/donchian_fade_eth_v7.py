from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "Failed Breakout Liquidity Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 36
        self.atr_period = 14
        self.rsi_period = 14
        self.min_wick_ratio = 0.55
        self.cooldown_bars = 16
        self.max_hold_bars = 12
        self.last_exit_bar = -100
        self.entry_bar = -100

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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        # Manage in-position time stop exit
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_held_time_exit",
                        "bars_held": bars_held,
                        "close": ctx.bar.close,
                    }
                )
            return None

        # Enforce multi-bar post-exit cooldown
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime safety check
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.60 or regime in ("CRISIS", "MELTDOWN"):
            return None

        req_len = max(self.lookback + 2, self.atr_period + 2, self.rsi_period + 2)
        highs = ctx.highs(req_len)
        lows = ctx.lows(req_len)
        closes = ctx.closes(req_len)

        if len(highs) < req_len or len(lows) < req_len or len(closes) < req_len:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.rsi_period)
        if atr is None or atr <= 0 or rsi is None:
            return None

        # Prior range high/low (strictly historical, excluding current bar)
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        cur_high = ctx.bar.high
        cur_low = ctx.bar.low
        cur_close = ctx.bar.close
        cur_open = ctx.bar.open

        # Bear Trap / Liquidity Sweep Long:
        # Intrabar low pierces significant range low, rejects and closes above range low with bullish candle body or rejection
        if cur_low < range_low and cur_close > range_low and cur_close >= cur_open:
            penetration = range_low - cur_low
            wick_ratio = penetration / atr
            # Require significant wick penetration + oversold/reversal RSI filter
            if wick_ratio >= self.min_wick_ratio and rsi <= 45.0:
                self.entry_bar = ctx.bar_index
                confidence = min(0.90, max(0.60, 0.60 + (wick_ratio * 0.20)))
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_breakdown_liquidity_sweep",
                        "range_low": range_low,
                        "cur_low": cur_low,
                        "cur_close": cur_close,
                        "penetration": penetration,
                        "wick_ratio": wick_ratio,
                        "rsi": rsi,
                        "atr": atr,
                    }
                )

        # Bull Trap / Liquidity Sweep Short:
        # Intrabar high pierces significant range high, rejects and closes below range high with bearish candle body or rejection
        if cur_high > range_high and cur_close < range_high and cur_close <= cur_open:
            penetration = cur_high - range_high
            wick_ratio = penetration / atr
            # Require significant wick penetration + overbought/exhaustion RSI filter
            if wick_ratio >= self.min_wick_ratio and rsi >= 55.0:
                self.entry_bar = ctx.bar_index
                confidence = min(0.90, max(0.60, 0.60 + (wick_ratio * 0.20)))
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "failed_breakout_liquidity_sweep",
                        "range_high": range_high,
                        "cur_high": cur_high,
                        "cur_close": cur_close,
                        "penetration": penetration,
                        "wick_ratio": wick_ratio,
                        "rsi": rsi,
                        "atr": atr,
                    }
                )

        return None