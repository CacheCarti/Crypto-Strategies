from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FailedBreakoutFade(Strategy):
    METADATA = {
        "name": "Failed Breakout Liquidity Fade",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback_period = 24
        self.atr_period = 14
        self.rsi_period = 14
        self.cooldown_bars = 6
        self.max_hold_bars = 7
        self.wick_atr_mult = 0.20
        self.last_exit_bar = -999
        self.entry_bar = -999

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

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -999

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        history_needed = self.lookback_period + 10
        closes = ctx.closes(history_needed)
        highs = ctx.highs(history_needed)
        lows = ctx.lows(history_needed)

        if len(closes) < history_needed:
            return None

        # Check regime filter: avoid entering during extreme market breakdown
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)

        current_close = ctx.bar.close
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_open = ctx.bar.open

        atr = self._atr(highs, lows, closes, self.atr_period)
        rsi = self._rsi(closes, self.rsi_period)
        mid_sma = self._sma(closes, self.lookback_period)

        if atr is None or rsi is None or mid_sma is None or atr <= 0.0:
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Time-based expiration exit
            if self.entry_bar > 0 and bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_max_hold_exit",
                        "bars_held": bars_held,
                        "current_close": current_close,
                        "mid_sma": mid_sma,
                        "rsi": rsi,
                    }
                )

            # Mean-reversion target reached
            if pos_dir == "long" and current_close >= mid_sma:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_take_profit_mid_sma_cross",
                        "bars_held": bars_held,
                        "current_close": current_close,
                        "mid_sma": mid_sma,
                        "rsi": rsi,
                    }
                )
            elif pos_dir == "short" and current_close <= mid_sma:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_take_profit_mid_sma_cross",
                        "bars_held": bars_held,
                        "current_close": current_close,
                        "mid_sma": mid_sma,
                        "rsi": rsi,
                    }
                )
            return None

        # Entry logic: Cooldown check
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Regime protection: skip trades during meltdown/crisis
        if market_regime in ["CRISIS", "MELTDOWN"] or crisis_score > 0.70:
            return None

        # Prior lookback window excluding current bar
        prior_highs = highs[-(self.lookback_period + 1):-1]
        prior_lows = lows[-(self.lookback_period + 1):-1]
        if len(prior_highs) < self.lookback_period or len(prior_lows) < self.lookback_period:
            return None

        channel_high = max(prior_highs)
        channel_low = min(prior_lows)
        min_penetration = self.wick_atr_mult * atr

        # Setup 1: Bear Trap (Bullish Sweep / Failed Breakdown)
        # Price swept below support by at least min_penetration, but closed back above support
        bear_trap = (current_low <= (channel_low - min_penetration)) and (current_close > channel_low) and (current_close >= current_open)

        # Setup 2: Bull Trap (Bearish Sweep / Failed Breakout)
        # Price swept above resistance by at least min_penetration, but closed back below resistance
        bull_trap = (current_high >= (channel_high + min_penetration)) and (current_close < channel_high) and (current_close <= current_open)

        if bear_trap and rsi < 62.0:
            sweep_depth_bps = ((channel_low - current_low) / channel_low) * 10000.0
            confidence = min(0.85, 0.55 + (sweep_depth_bps / 500.0))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bear_trap_failed_breakdown_fade",
                    "channel_low": channel_low,
                    "sweep_low": current_low,
                    "sweep_depth_bps": sweep_depth_bps,
                    "atr": atr,
                    "rsi": rsi,
                    "mid_sma": mid_sma,
                }
            )

        if bull_trap and rsi > 38.0:
            sweep_height_bps = ((current_high - channel_high) / channel_high) * 10000.0
            confidence = min(0.85, 0.55 + (sweep_height_bps / 500.0))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "bull_trap_failed_breakout_fade",
                    "channel_high": channel_high,
                    "sweep_high": current_high,
                    "sweep_height_bps": sweep_height_bps,
                    "atr": atr,
                    "rsi": rsi,
                    "mid_sma": mid_sma,
                }
            )

        return None