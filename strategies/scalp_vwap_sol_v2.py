from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolVwapReversionScalp(Strategy):
    METADATA = {
        "name": "SolVwapReversionScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 170.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 45,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 30
        self.rsi_period = 10
        self.dev_threshold_pct = 0.85
        self.cooldown_bars = 28
        self.last_exit_bar = -999
        self.last_entry_bar = -999

    def _vwap(self, highs, lows, closes, volumes, period):
        if len(closes) < period:
            return None
        typical_prices = [
            (h + l + c) / 3.0
            for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])
        ]
        vols = volumes[-period:]
        total_pv = sum(tp * v for tp, v in zip(typical_prices, vols))
        total_v = sum(vols)
        if total_v <= 0.0:
            return None
        return total_pv / total_v

    def _rsi(self, closes, period):
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

        closes = ctx.closes(self.vwap_period + 5)
        highs = ctx.highs(self.vwap_period + 5)
        lows = ctx.lows(self.vwap_period + 5)
        volumes = ctx.volumes(self.vwap_period + 5)

        if len(closes) < self.vwap_period:
            return None

        vwap_val = self._vwap(highs, lows, closes, volumes, self.vwap_period)
        if vwap_val is None or vwap_val <= 0.0:
            return None

        rsi_val = self._rsi(closes, self.rsi_period)
        if rsi_val is None:
            return None

        current_price = ctx.bar.close
        dev_pct = ((current_price - vwap_val) / vwap_val) * 100.0
        funding = ctx.features.get("funding_rate_solusdt", 0.0)
        crisis_score = ctx.market.get("crisis_score", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # In-position management: take profit on full mean reversion back to VWAP
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_price >= vwap_val:
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "long_tp_vwap_touch",
                        "price": current_price,
                        "vwap": round(vwap_val, 3),
                        "dev_pct": round(dev_pct, 3),
                        "rsi": round(rsi_val, 2),
                    },
                )
            elif pos_dir == "short" and current_price <= vwap_val:
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "short_tp_vwap_touch",
                        "price": current_price,
                        "vwap": round(vwap_val, 3),
                        "dev_pct": round(dev_pct, 3),
                        "rsi": round(rsi_val, 2),
                    },
                )
            return None

        # Hard Cooldown to cap trade frequency and protect against friction
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Filter high-stress and breakdown market states
        if crisis_score > 0.40 or regime in ("CRISIS", "MELTDOWN"):
            return None

        # Volume confirmation: ensure significant liquidity/activity
        avg_vol = sum(volumes[-10:]) / 10.0 if len(volumes) >= 10 else 1.0
        vol_ratio = (ctx.bar.volume / avg_vol) if avg_vol > 0 else 1.0
        if vol_ratio < 0.8:
            return None

        # Long Setup: Extreme dislocation below VWAP (-0.85%) + Deep RSI Oversold (<=24)
        if dev_pct <= -self.dev_threshold_pct and rsi_val <= 24.0:
            confidence = min(0.92, 0.60 + abs(dev_pct) * 0.25)
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_vwap_extreme_oversold_reversal",
                    "dev_pct": round(dev_pct, 3),
                    "vwap": round(vwap_val, 3),
                    "rsi": round(rsi_val, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "funding": funding,
                },
            )

        # Short Setup: Extreme dislocation above VWAP (+0.85%) + Deep RSI Overbought (>=76)
        if dev_pct >= self.dev_threshold_pct and rsi_val >= 76.0:
            confidence = min(0.92, 0.60 + abs(dev_pct) * 0.25)
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_vwap_extreme_overbought_fade",
                    "dev_pct": round(dev_pct, 3),
                    "vwap": round(vwap_val, 3),
                    "rsi": round(rsi_val, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "funding": funding,
                },
            )

        return None