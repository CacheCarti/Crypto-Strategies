from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolVwapDeviationScalp(Strategy):
    METADATA = {
        "name": "SolVwapDeviationScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 150.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 40,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 28
        self.rsi_period = 14
        self.dev_threshold_pct = 0.85   # 85 bps extreme deviation required
        self.cooldown_bars = 48         # 4-hour cooldown after position close
        self.last_trade_bar = -100

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
        if total_v <= 0:
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vwap_period + self.rsi_period + 5)
        if len(closes) < self.vwap_period + self.rsi_period:
            return None

        highs = ctx.highs(self.vwap_period)
        lows = ctx.lows(self.vwap_period)
        volumes = ctx.volumes(self.vwap_period)

        vwap = self._vwap(highs, lows, closes, volumes, self.vwap_period)
        if vwap is None or vwap <= 0:
            return None

        current_price = ctx.bar.close
        current_open = ctx.bar.open
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        dev_pct = ((current_price - vwap) / vwap) * 100.0

        # Position management: Take profit at VWAP reversion touch
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_price >= vwap:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "vwap_mean_reversion_target_hit_long",
                        "price": current_price,
                        "vwap": vwap,
                        "rsi": rsi,
                        "dev_pct": dev_pct,
                    },
                )
            elif direction == "short" and current_price <= vwap:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "vwap_mean_reversion_target_hit_short",
                        "price": current_price,
                        "vwap": vwap,
                        "rsi": rsi,
                        "dev_pct": dev_pct,
                    },
                )
            return None

        # Mandatory hard cooldown between trades
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Regime & Crisis filters
        regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.40:
            return None

        # Long Setup: Deep stretch below VWAP + Extreme Oversold RSI + Bullish candle reversal confirmation
        if dev_pct <= -self.dev_threshold_pct and rsi <= 27.0 and current_price > current_open:
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.92, 0.65 + abs(dev_pct) * 0.20)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_vwap_extreme_oversold_bounce",
                    "price": current_price,
                    "vwap": vwap,
                    "dev_pct": dev_pct,
                    "rsi": rsi,
                    "crisis_score": crisis_score,
                },
            )

        # Short Setup: High stretch above VWAP + Extreme Overbought RSI + Bearish candle reversal confirmation
        if dev_pct >= self.dev_threshold_pct and rsi >= 73.0 and current_price < current_open:
            self.last_trade_bar = ctx.bar_index
            confidence = min(0.92, 0.65 + abs(dev_pct) * 0.20)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "sol_vwap_extreme_overbought_fade",
                    "price": current_price,
                    "vwap": vwap,
                    "dev_pct": dev_pct,
                    "rsi": rsi,
                    "crisis_score": crisis_score,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index