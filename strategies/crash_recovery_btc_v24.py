from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcPanicRecovery(Strategy):
    METADATA = {
        "name": "BtcPanicRecovery",
        "domain": "btc_usdc",
        "declared_sl_bps": 350.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 28800,  # ~8 hours target hold
        "warmup_bars": 30,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 24
        self.flush_threshold_pct = -0.05  # -5.0% drop over 24 bars
        self.max_hold_bars = 12
        self.cooldown_period = 6
        self.entry_bar = -1
        self.last_exit_bar = -100
        self.entry_high_24 = 0.0
        self.entry_low_24 = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 2)
        highs = ctx.highs(self.lookback + 2)
        lows = ctx.lows(self.lookback + 2)

        if len(closes) < self.lookback + 2:
            return None

        # Manage existing open position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar != -1 else 0
            curr_close = closes[-1]
            midpoint = (self.entry_high_24 + self.entry_low_24) / 2.0

            # Exit condition 1: reached 24-bar range midpoint
            if self.entry_high_24 > self.entry_low_24 and curr_close >= midpoint:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "flush_midpoint_reached",
                        "close": curr_close,
                        "midpoint": round(midpoint, 2),
                        "bars_held": bars_held,
                    },
                )

            # Exit condition 2: time-based exit to free capital & avoid protracted bleed
            if bars_held >= self.max_hold_bars:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "max_bars_expired",
                        "close": curr_close,
                        "bars_held": bars_held,
                    },
                )

            return None

        # Cooldown guard after exiting a trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Regime safety check: avoid meltdown or extreme volatility
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime == "MELTDOWN":
            return None

        # Measure 24-bar flush
        c_now = closes[-1]
        c_24_ago = closes[-25]
        ret_24 = (c_now - c_24_ago) / c_24_ago

        # Entry setup: flush condition met
        if ret_24 <= self.flush_threshold_pct:
            prev_high = highs[-2]
            # Capitulation reversal trigger: current close breaks above previous bar's high
            if c_now > prev_high:
                rsi_val = self._rsi(closes, 14)
                rsi_safe = rsi_val if rsi_val is not None else 35.0

                # Ensure we are recovering from an oversold condition, not entering overbought
                if rsi_safe < 55.0:
                    recent_highs = highs[-self.lookback:]
                    recent_lows = lows[-self.lookback:]
                    self.entry_high_24 = max(recent_highs)
                    self.entry_low_24 = min(recent_lows)
                    self.entry_bar = ctx.bar_index

                    fg = ctx.features.get("fear_greed_index", 50.0)
                    confidence = 0.65
                    if fg < 30.0:
                        confidence = 0.8  # Elevated confidence during extreme market panic

                    return ctx.signal(
                        "long",
                        confidence=confidence,
                        stop_loss_bps=self.METADATA["declared_sl_bps"],
                        take_profit_bps=self.METADATA["declared_tp_bps"],
                        horizon_seconds=self.METADATA["declared_hold_seconds"],
                        metadata={
                            "reason": "flush_capitulation_reversal",
                            "return_24_pct": round(ret_24 * 100, 2),
                            "prev_high": prev_high,
                            "close": c_now,
                            "rsi": round(rsi_safe, 2),
                            "fear_greed": fg,
                        },
                    )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index
        self.entry_bar = -1