from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "FailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 36
        self.atr_period = 14
        self.rsi_period = 14
        self.min_wick_atr_mult = 0.50
        self.cooldown_bars = 14
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
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = max(self.lookback + 2, self.atr_period + 2, self.rsi_period + 2, 25)
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)
        volumes = ctx.volumes(req_bars)

        if len(closes) < req_bars:
            return None

        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_close = ctx.bar.close
        current_vol = ctx.bar.volume

        # Position management
        if ctx.has_position():
            if self.entry_bar > 0 and (ctx.bar_index - self.entry_bar) >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "time_stop_hold_limit_reached", "bars_held": ctx.bar_index - self.entry_bar}
                )
            return None

        # Hard cooldown guard between trades
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("MELTDOWN", "CRISIS") or crisis_score > 0.60:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.rsi_period)
        if atr is None or atr <= 0.0 or rsi is None:
            return None

        # Volume threshold: require above-average volume to confirm liquidity flush/absorption
        avg_vol = sum(volumes[-20:]) / 20.0
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
        if vol_ratio < 1.1:
            return None

        # Calculate high/low of prior range excluding current bar
        prior_highs = highs[-(self.lookback + 1):-1]
        prior_lows = lows[-(self.lookback + 1):-1]
        range_high = max(prior_highs)
        range_low = min(prior_lows)

        min_wick = atr * self.min_wick_atr_mult

        # Failed Breakdown / Bear Trap -> Long
        # Requirements:
        # 1. Poked below prior range low by at least 0.50 ATR
        # 2. Closed back inside the range (above range_low) and formed a bullish hammer/rejection (close >= open)
        # 3. RSI is not overbought (< 48)
        if current_low <= (range_low - min_wick) and current_close > range_low and current_close >= current_open:
            if rsi < 48.0:
                wick_depth = range_low - current_low
                wick_ratio = min(wick_depth / atr, 2.5)
                confidence = max(0.60, min(0.85, 0.60 + (wick_ratio - self.min_wick_atr_mult) * 0.15))

                sl_dist = current_close - current_low + (atr * 0.15)
                sl_bps = max(140.0, min(320.0, (sl_dist / current_close) * 10000.0))
                tp_bps = sl_bps * 1.75

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "failed_breakdown_liquidity_sweep_long",
                        "range_low": range_low,
                        "wick_depth": wick_depth,
                        "atr": atr,
                        "rsi": rsi,
                        "vol_ratio": vol_ratio,
                        "close": current_close
                    }
                )

        # Failed Breakout / Bull Trap -> Short
        # Requirements:
        # 1. Poked above prior range high by at least 0.50 ATR
        # 2. Closed back inside the range (below range_high) and formed a bearish shooting star/rejection (close <= open)
        # 3. RSI is not oversold (> 52)
        if current_high >= (range_high + min_wick) and current_close < range_high and current_close <= current_open:
            if rsi > 52.0:
                wick_height = current_high - range_high
                wick_ratio = min(wick_height / atr, 2.5)
                confidence = max(0.60, min(0.85, 0.60 + (wick_ratio - self.min_wick_atr_mult) * 0.15))

                sl_dist = current_high - current_close + (atr * 0.15)
                sl_bps = max(140.0, min(320.0, (sl_dist / current_close) * 10000.0))
                tp_bps = sl_bps * 1.75

                self.entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=sl_bps,
                    take_profit_bps=tp_bps,
                    metadata={
                        "reason": "failed_breakout_liquidity_sweep_short",
                        "range_high": range_high,
                        "wick_height": wick_height,
                        "atr": atr,
                        "rsi": rsi,
                        "vol_ratio": vol_ratio,
                        "close": current_close
                    }
                )

        return None