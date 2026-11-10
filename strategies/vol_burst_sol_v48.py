from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVolBurstCrossRegime(Strategy):
    METADATA = {
        "name": "SolVolBurstCrossRegime",
        "domain": "sol_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 560.0,
        "declared_hold_seconds": 14400,  # ~4 hours target hold
        "warmup_bars": 35,
        "required_features": ["btc_return_pct"]
    }

    def initialize(self, ctx: BarContext) -> None:
        self.warmup = 30
        self.cooldown_bars = 6
        self.last_exit_bar = -100
        self.entry_bar = -100

    def _atr(self, highs, lows, closes, period=14):
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
        closes = ctx.closes(self.warmup + 5)
        if len(closes) < self.warmup + 2:
            return None

        highs = ctx.highs(self.warmup + 5)
        lows = ctx.lows(self.warmup + 5)
        volumes = ctx.volumes(self.warmup + 5)

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume

        # Dynamic exit management when in position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar
            direction = ctx.position_direction()

            # Time stop / momentum exhaustion check after 4 bars
            if bars_held >= 4:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "max_bars_exhaustion_flat",
                        "bars_held": bars_held,
                        "price": current_close
                    }
                )

            # Stalling momentum exit: opposing high range reversal
            body = abs(current_close - current_open)
            candle_range = current_high - current_low
            if candle_range > 0 and (body / candle_range) > 0.6:
                if direction == "long" and current_close < current_open:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "long_momentum_stall_reversal",
                            "bars_held": bars_held,
                            "price": current_close
                        }
                    )
                elif direction == "short" and current_close > current_open:
                    return ctx.signal(
                        "flat",
                        confidence=0.65,
                        metadata={
                            "reason": "short_momentum_stall_reversal",
                            "bars_held": bars_held,
                            "price": current_close
                        }
                    )
            return None

        # Cooldown guard after closing a trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Base calculations for volume and range
        vol_slice = volumes[-21:-1]
        avg_vol = sum(vol_slice) / len(vol_slice)
        if avg_vol <= 0:
            return None

        vol_ratio = current_vol / avg_vol
        atr = self._atr(highs[:-1], lows[:-1], closes[:-1], period=14)
        if atr is None or atr <= 0:
            return None

        current_range = current_high - current_low
        range_ratio = current_range / atr

        # Strong Volume Burst & Unusually Wide Range Condition
        if vol_ratio < 2.0 or range_ratio < 1.6:
            return None

        # Decisive close: candle body must represent at least 60% of total candle range
        body = abs(current_close - current_open)
        if current_range == 0 or (body / current_range) < 0.6:
            return None

        # Novel confirmation: Cross-Asset Momentum & Market Volatility State
        # Prior failure addressed by requiring macro market alignment (BTC return) and safe volatility regime
        btc_ret = ctx.features.get("btc_return_pct", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)

        # Avoid entering during disorderly panic melts
        if crisis_score > 0.6:
            return None

        # Long Trigger: Bullish breakout bar + BTC supportive
        if current_close > current_open and btc_ret >= -0.5:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=min(0.9, 0.55 + (vol_ratio / 10.0)),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "vol_burst_expansion_long",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "btc_return_pct": round(btc_ret, 2),
                    "price": current_close
                }
            )

        # Short Trigger: Bearish breakdown bar + BTC confirming weakness
        elif current_close < current_open and btc_ret <= 0.5:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=min(0.9, 0.55 + (vol_ratio / 10.0)),
                stop_loss_bps=280.0,
                take_profit_bps=560.0,
                horizon_seconds=14400,
                metadata={
                    "reason": "vol_burst_expansion_short",
                    "vol_ratio": round(vol_ratio, 2),
                    "range_ratio": round(range_ratio, 2),
                    "btc_return_pct": round(btc_ret, 2),
                    "price": current_close
                }
            )

        return None