from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "FailedBreakoutFade",
        "domain": "eth_usdc",
        "declared_sl_bps": 240.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 36
        self.atr_period = 14
        self.min_wick_atr_mult = 0.45
        self.cooldown_bars = 12
        self.last_exit_bar = -999
        self.bars_in_trade = 0
        self.max_hold_bars = 8

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
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
        self.bars_in_trade = 0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        needed = self.lookback + self.atr_period + 5
        closes = ctx.closes(needed)
        highs = ctx.highs(needed)
        lows = ctx.lows(needed)

        if len(closes) < needed:
            return None

        # Position management
        if ctx.has_position():
            self.bars_in_trade += 1
            if self.bars_in_trade >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_horizon_exit",
                        "bars_in_trade": self.bars_in_trade,
                        "close": round(ctx.bar.close, 2),
                    },
                )
            return None
        else:
            self.bars_in_trade = 0

        # Mandatory post-exit cooldown guard to prevent overtrading & friction bleed
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime & Crisis Filter
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            return None

        crisis_score = ctx.market.get("crisis_score", 0.0)
        if crisis_score > 0.40:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or atr <= 0:
            return None

        rsi = self._rsi(closes, 14)

        # Historical reference window (strictly excluding the current bar)
        ref_highs = highs[-self.lookback - 1 : -1]
        ref_lows = lows[-self.lookback - 1 : -1]
        if len(ref_highs) < self.lookback or len(ref_lows) < self.lookback:
            return None

        prior_high = max(ref_highs)
        prior_low = min(ref_lows)

        cur_high = ctx.bar.high
        cur_low = ctx.bar.low
        cur_close = ctx.bar.close
        bar_range = cur_high - cur_low

        if bar_range <= 0:
            return None

        min_wick = atr * self.min_wick_atr_mult

        # FAILED BULL BREAKOUT (SHORT SETUP)
        # 1. Price penetrated above the key multi-bar high by at least meaningful ATR threshold (liquidity hunt)
        # 2. Closed back strictly below the prior high
        # 3. Strong rejection wick (upper wick >= 50% of total bar range or close in bottom 35%)
        # 4. RSI indicates overbought exhaustion (RSI >= 52)
        upper_wick = cur_high - max(ctx.bar.open, cur_close)
        is_bull_trap = (
            cur_high >= prior_high + min_wick
            and cur_close < prior_high
            and (upper_wick >= 0.50 * bar_range or (cur_close - cur_low) <= 0.35 * bar_range)
            and rsi >= 52.0
        )

        if is_bull_trap:
            wick_size = cur_high - prior_high
            wick_atr_ratio = wick_size / atr
            confidence = min(0.90, max(0.55, 0.60 + 0.15 * (wick_atr_ratio - self.min_wick_atr_mult)))
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bull_trap_failed_breakout_short",
                    "prior_high": round(prior_high, 2),
                    "cur_high": round(cur_high, 2),
                    "cur_close": round(cur_close, 2),
                    "wick_size": round(wick_size, 2),
                    "wick_atr_ratio": round(wick_atr_ratio, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 2),
                },
            )

        # FAILED BEAR BREAKDOWN (LONG SETUP)
        # 1. Price penetrated below the key multi-bar low by at least meaningful ATR threshold (stop sweep)
        # 2. Closed back strictly above the prior low
        # 3. Strong rejection wick (lower wick >= 50% of total bar range or close in top 35%)
        # 4. RSI indicates oversold exhaustion (RSI <= 48)
        lower_wick = min(ctx.bar.open, cur_close) - cur_low
        is_bear_trap = (
            cur_low <= prior_low - min_wick
            and cur_close > prior_low
            and (lower_wick >= 0.50 * bar_range or (cur_high - cur_close) <= 0.35 * bar_range)
            and rsi <= 48.0
        )

        if is_bear_trap:
            wick_size = prior_low - cur_low
            wick_atr_ratio = wick_size / atr
            confidence = min(0.90, max(0.55, 0.60 + 0.15 * (wick_atr_ratio - self.min_wick_atr_mult)))
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bear_trap_failed_breakdown_long",
                    "prior_low": round(prior_low, 2),
                    "cur_low": round(cur_low, 2),
                    "cur_close": round(cur_close, 2),
                    "wick_size": round(wick_size, 2),
                    "wick_atr_ratio": round(wick_atr_ratio, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 2),
                },
            )

        return None