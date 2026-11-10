from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolumeBurstMomentum(Strategy):
    METADATA = {
        "name": "SolVolumeBurstMomentum",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 480.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 50,
        "required_features": ["btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999
        self.max_hold_bars = 5

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _atr(self, highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = self.METADATA["warmup_bars"]
        closes = ctx.closes(warmup)
        if len(closes) < warmup:
            return None

        # Position management
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            curr_close = ctx.bar.close
            curr_open = ctx.bar.open
            pos_dir = ctx.position_direction()

            # Time-based max hold exit
            if bars_held >= self.max_hold_bars:
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={"reason": "max_bars_reached", "bars_held": bars_held, "close": curr_close}
                )

            # Momentum stall exit: counter-directional bar with solid body
            if pos_dir == "long" and curr_close < curr_open:
                body = curr_open - curr_close
                candle_range = ctx.bar.high - ctx.bar.low
                if candle_range > 0 and (body / candle_range) > 0.55:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "long_momentum_stalled", "close": curr_close, "bars_held": bars_held}
                    )
            elif pos_dir == "short" and curr_close > curr_open:
                body = curr_close - curr_open
                candle_range = ctx.bar.high - ctx.bar.low
                if candle_range > 0 and (body / candle_range) > 0.55:
                    return ctx.signal(
                        "flat",
                        confidence=0.5,
                        metadata={"reason": "short_momentum_stalled", "close": curr_close, "bars_held": bars_held}
                    )
            return None

        # Cooldown guard after exits
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Crisis / extreme volatility safety guard
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.65:
            return None

        volumes = ctx.volumes(30)
        highs = ctx.highs(30)
        lows = ctx.lows(30)

        vol_avg = self._sma(volumes[:-1], 20)
        atr14 = self._atr(highs[:-1], lows[:-1], closes[:-1], 14)
        ema30 = self._sma(closes, 30)

        if vol_avg is None or atr14 is None or ema30 is None:
            return None

        curr_vol = ctx.bar.volume
        curr_high = ctx.bar.high
        curr_low = ctx.bar.low
        curr_open = ctx.bar.open
        curr_close = ctx.bar.close
        curr_range = curr_high - curr_low

        # Volume-burst and expansion criteria
        vol_ratio = curr_vol / vol_avg if vol_avg > 0 else 0.0
        range_ratio = curr_range / atr14 if atr14 > 0 else 0.0

        if vol_ratio < 2.1 or range_ratio < 1.7:
            return None

        body = abs(curr_close - curr_open)
        if curr_range == 0 or (body / curr_range) < 0.60:
            return None

        # Cross-asset trend confirmation via BTC 24h return feature
        btc_ret = ctx.features.get("btc_return_pct", 0.0)

        # Bullish burst
        if curr_close > curr_open and curr_close > ema30 and btc_ret >= -0.5:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (vol_ratio - 2.0) * 0.1)
            return ctx.signal(
                "long",
                confidence=round(conf, 2),
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                metadata={
                    "reason": "volume_burst_bullish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "btc_ret": round(btc_ret, 2),
                    "close": curr_close,
                }
            )

        # Bearish burst
        if curr_close < curr_open and curr_close < ema30 and btc_ret <= 0.5:
            self.entry_bar = ctx.bar_index
            conf = min(0.9, 0.55 + (vol_ratio - 2.0) * 0.1)
            return ctx.signal(
                "short",
                confidence=round(conf, 2),
                stop_loss_bps=280.0,
                take_profit_bps=480.0,
                metadata={
                    "reason": "volume_burst_bearish_expansion",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "btc_ret": round(btc_ret, 2),
                    "close": curr_close,
                }
            )

        return None