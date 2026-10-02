from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVwapReversionScalp(Strategy):
    METADATA = {
        "name": "SOL VWAP Reversion Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 24
        self.rsi_period = 14
        self.dev_threshold_bps = 80.0
        self.rsi_oversold = 28.0
        self.rsi_overbought = 72.0
        self.cooldown_bars = 36
        self.last_exit_bar = -100

    def _vwap(self, highs, lows, closes, volumes, period):
        if len(closes) < period:
            return None
        typical_prices = [(h + l + c) / 3.0 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
        vols = volumes[-period:]
        total_pv = sum(tp * v for tp, v in zip(typical_prices, vols))
        total_v = sum(vols)
        if total_v == 0:
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
        if ctx.bar_index < self.METADATA["warmup_bars"]:
            return None

        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN") or ctx.regime in ("crisis", "volatile"):
            return None

        closes = ctx.closes(self.vwap_period + self.rsi_period + 2)
        highs = ctx.highs(self.vwap_period + 2)
        lows = ctx.lows(self.vwap_period + 2)
        volumes = ctx.volumes(self.vwap_period + 2)

        vwap = self._vwap(highs, lows, closes, volumes, self.vwap_period)
        rsi = self._rsi(closes, self.rsi_period)

        if vwap is None or rsi is None or vwap <= 0:
            return None

        current_price = ctx.bar.close
        dev_bps = ((current_price - vwap) / vwap) * 10000.0

        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_price >= vwap:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "vwap_target_reached_long",
                        "price": current_price,
                        "vwap": round(vwap, 4),
                        "rsi": round(rsi, 2),
                    },
                )
            elif direction == "short" and current_price <= vwap:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "vwap_target_reached_short",
                        "price": current_price,
                        "vwap": round(vwap, 4),
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Reversal candle confirmation to avoid catching falling knives
        is_bullish_reversal = ctx.bar.close > ctx.bar.open
        is_bearish_reversal = ctx.bar.close < ctx.bar.open

        # Strict high-conviction long entry
        if dev_bps <= -self.dev_threshold_bps and rsi <= self.rsi_oversold and is_bullish_reversal:
            return ctx.signal(
                "long",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_vwap_stretch_oversold_bounce",
                    "price": current_price,
                    "vwap": round(vwap, 4),
                    "dev_bps": round(dev_bps, 2),
                    "rsi": round(rsi, 2),
                },
            )

        # Strict high-conviction short entry
        if dev_bps >= self.dev_threshold_bps and rsi >= self.rsi_overbought and is_bearish_reversal:
            return ctx.signal(
                "short",
                confidence=0.85,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "extreme_vwap_stretch_overbought_fade",
                    "price": current_price,
                    "vwap": round(vwap, 4),
                    "dev_bps": round(dev_bps, 2),
                    "rsi": round(rsi, 2),
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index