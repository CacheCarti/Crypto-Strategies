from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 18000,  # ~5 hours
        "warmup_bars": 35,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vol_period = 20
        self.atr_period = 14
        self.vol_multiplier = 2.1
        self.range_multiplier = 1.6
        self.max_hold_bars = 6
        self.cooldown_bars = 4
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(30)
        volumes = ctx.volumes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Manage active position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            current_close = ctx.bar.close
            pos_dir = ctx.position_direction()

            # Time-based structural exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "timed_exit", "bars_held": bars_held, "close": current_close}
                )

            # Momentum exhaustion exit: reversal bar closing opposite to trend
            if bars_held >= 2:
                if pos_dir == "long" and ctx.bar.close < ctx.bar.open and (ctx.bar.open - ctx.bar.close) > (ctx.bar.high - ctx.bar.low) * 0.6:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "momentum_exhaustion_bear_candle", "bars_held": bars_held}
                    )
                elif pos_dir == "short" and ctx.bar.close > ctx.bar.open and (ctx.bar.close - ctx.bar.open) > (ctx.bar.high - ctx.bar.low) * 0.6:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "momentum_exhaustion_bull_candle", "bars_held": bars_held}
                    )
            return None

        # Cooldown guard after exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Market regime filter: avoid entering high-burst moves during meltdown/crisis
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Indicator calculations
        avg_vol = sum(volumes[-self.vol_period - 1:-1]) / self.vol_period
        current_vol = ctx.bar.volume
        atr = self._atr(highs, lows, closes, self.atr_period)
        if atr is None or avg_vol <= 0:
            return None

        current_range = ctx.bar.high - ctx.bar.low
        body_size = abs(ctx.bar.close - ctx.bar.open)

        # Volume burst + unusually wide range + decisive body
        is_vol_burst = current_vol >= avg_vol * self.vol_multiplier
        is_wide_range = current_range >= atr * self.range_multiplier
        is_decisive_body = body_size >= (current_range * 0.60)

        if not (is_vol_burst and is_wide_range and is_decisive_body):
            return None

        # Structural volatility-based SL/TP (derived from ATR in bps)
        atr_bps = (atr / ctx.bar.close) * 10000.0
        stop_loss_bps = max(180.0, min(atr_bps * 1.5, 380.0))
        take_profit_bps = stop_loss_bps * 2.0

        vol_ratio = round(current_vol / avg_vol, 2)
        confidence = min(0.85, 0.55 + (vol_ratio - self.vol_multiplier) * 0.05)

        # Bullish burst: close strongly near top of bar
        if ctx.bar.close > ctx.bar.open and (ctx.bar.close - ctx.bar.low) >= (current_range * 0.75):
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_vol_burst_expansion",
                    "vol_ratio": vol_ratio,
                    "range_atr_ratio": round(current_range / atr, 2),
                    "atr_bps": round(atr_bps, 1),
                    "close": ctx.bar.close,
                }
            )

        # Bearish burst: close strongly near bottom of bar
        if ctx.bar.close < ctx.bar.open and (ctx.bar.high - ctx.bar.close) >= (current_range * 0.75):
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_vol_burst_expansion",
                    "vol_ratio": vol_ratio,
                    "range_atr_ratio": round(current_range / atr, 2),
                    "atr_bps": round(atr_bps, 1),
                    "close": ctx.bar.close,
                }
            )

        return None