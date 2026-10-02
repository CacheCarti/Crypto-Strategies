from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class TripleTouchSupport(Strategy):
    METADATA = {
        "name": "TripleTouchSupport",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 21600,
        "warmup_bars": 60,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 48
        self.touch_tol_pct = 0.0035  # within 0.35% (35 bps) of support level
        self.min_touch_spacing = 8   # minimum bars between separate touches
        self.min_bounce_pct = 0.010   # must bounce at least 1.0% between touches to be distinct
        self.cooldown_bars = 20       # hard cooldown after exit to prevent overtrading
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _rsi(self, closes, period=14) -> Optional[float]:
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

    def _count_valid_prior_touches(self, highs, lows, closes, support_level: float) -> int:
        n = len(lows)
        start_idx = max(0, n - self.lookback)
        end_idx = max(0, n - 2)

        touches = []
        had_bounce_since_last_touch = True

        for i in range(start_idx, end_idx):
            is_touch = (
                lows[i] <= support_level * (1.0 + self.touch_tol_pct)
                and closes[i] >= support_level * 0.996
            )
            
            if is_touch:
                if not touches:
                    touches.append(i)
                    had_bounce_since_last_touch = False
                elif (i - touches[-1] >= self.min_touch_spacing) and had_bounce_since_last_touch:
                    touches.append(i)
                    had_bounce_since_last_touch = False
            else:
                # Check if price rallied meaningfully away from support before next test
                if highs[i] >= support_level * (1.0 + self.min_bounce_pct):
                    had_bounce_since_last_touch = True

        return len(touches)

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 10)
        highs = ctx.highs(self.lookback + 10)
        lows = ctx.lows(self.lookback + 10)
        opens = ctx.opens(self.lookback + 10)

        if len(closes) < self.lookback + 5:
            return None

        current_close = ctx.bar.close
        current_low = ctx.bar.low
        current_high = ctx.bar.high
        current_open = ctx.bar.open

        rsi = self._rsi(closes, 14)
        if rsi is None:
            return None

        # Position management
        if ctx.has_position():
            # Clean take-profit exit on strong RSI momentum
            if rsi > 70.0:
                return ctx.signal(
                    "flat",
                    confidence=0.75,
                    metadata={
                        "reason": "rsi_overbought_take_profit",
                        "rsi": round(rsi, 2),
                        "price": current_close,
                    },
                )
            return None

        # Cooldown guard: prevent rapid-fire entries
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering during extreme market stress
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Calculate structural support level from lookback window (excluding last 2 bars)
        hist_lows = lows[-(self.lookback):-2]
        if not hist_lows:
            return None
        support_level = min(hist_lows)

        # Count confirmed prior touches that experienced intervening bounces
        prior_touches = self._count_valid_prior_touches(highs, lows, closes, support_level)

        # We look specifically for the 3rd test (2 prior confirmed touches)
        if prior_touches == 2:
            prev_low = lows[-2]
            # 3rd test probe into support zone
            tested_support = (current_low <= support_level * (1.0 + self.touch_tol_pct)) or (
                prev_low <= support_level * (1.0 + self.touch_tol_pct)
            )

            # Rejection / bounce verification:
            # 1. Close above support
            # 2. Bullish candle action (close above open and above midpoint of bar)
            # 3. Not chasing: close remains within 0.8% of support
            candle_mid = (current_high + current_low) / 2.0
            rejection_candle = (current_close > current_open) and (current_close >= candle_mid)
            held_support = current_close > support_level
            within_entry_zone = current_close <= support_level * 1.008

            # Momentum verification: RSI oversold-to-neutral (rebounding, not blown out)
            rsi_favorable = 32.0 <= rsi <= 52.0

            if tested_support and held_support and rejection_candle and within_entry_zone and rsi_favorable:
                dist_bps = ((current_close - support_level) / current_close) * 10000.0
                dynamic_sl = max(160.0, min(240.0, dist_bps + 60.0))
                dynamic_tp = max(380.0, dynamic_sl * 2.2)

                self.last_entry_bar = ctx.bar_index

                return ctx.signal(
                    "long",
                    confidence=0.80,
                    stop_loss_bps=dynamic_sl,
                    take_profit_bps=dynamic_tp,
                    metadata={
                        "reason": "triple_touch_support_rejection",
                        "support_level": round(support_level, 2),
                        "prior_touches": prior_touches,
                        "rsi": round(rsi, 2),
                        "price": current_close,
                        "distance_bps": round(dist_bps, 1),
                        "regime": regime,
                    },
                )

        return None