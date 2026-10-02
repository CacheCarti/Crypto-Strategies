from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVwapMeanReversionScalp(Strategy):
    METADATA = {
        "name": "SOL VWAP Mean Reversion Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 110.0,
        "declared_tp_bps": 140.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 40,
        "required_features": ["funding_rate_solusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 30
        self.rsi_period = 14
        self.dev_threshold = 0.0085  # 85 bps deviation from rolling VWAP
        self.rsi_oversold = 28.0
        self.rsi_overbought = 72.0
        self.cooldown_bars = 28      # 140 minutes cooldown to strictly control trade frequency
        self.last_entry_bar = -100
        self.last_exit_bar = -100

    def _calc_vwap(self, highs, lows, closes, volumes, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        typical_prices = [(h + l + c) / 3.0 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
        vols = volumes[-period:]
        total_pv = sum(tp * v for tp, v in zip(typical_prices, vols))
        total_v = sum(vols)
        if total_v <= 0:
            return None
        return total_pv / total_v

    def _calc_rsi(self, closes, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        highs = ctx.highs(self.METADATA["warmup_bars"])
        lows = ctx.lows(self.METADATA["warmup_bars"])
        volumes = ctx.volumes(self.METADATA["warmup_bars"])

        vwap = self._calc_vwap(highs, lows, closes, volumes, self.vwap_period)
        rsi = self._calc_rsi(closes, self.rsi_period)

        if vwap is None or rsi is None or vwap <= 0:
            return None

        current_price = ctx.bar.close
        deviation = (current_price - vwap) / vwap
        funding_rate = ctx.features.get("funding_rate_solusdt", 0.0)
        regime = ctx.market.get("regime", "NORMAL")

        # In-position management: exit on full mean reversion back to VWAP
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_price >= vwap:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "vwap_mean_reverted_long",
                        "price": current_price,
                        "vwap": round(vwap, 4),
                        "deviation_bps": round(deviation * 10000, 2),
                        "rsi": round(rsi, 2),
                    },
                )
            elif pos_dir == "short" and current_price <= vwap:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.80,
                    metadata={
                        "reason": "vwap_mean_reverted_short",
                        "price": current_price,
                        "vwap": round(vwap, 4),
                        "deviation_bps": round(deviation * 10000, 2),
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # Hard Cooldown Filters
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        if bars_since_entry < self.cooldown_bars or bars_since_exit < self.cooldown_bars:
            return None

        # Filter out extreme crisis / meltdown conditions
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # High-conviction Long Entry: Deep VWAP discount + confirmed oversold RSI
        if deviation < -self.dev_threshold and rsi < self.rsi_oversold:
            if funding_rate < 0.0005:
                confidence = min(0.95, 0.70 + abs(deviation) * 15.0)
                self.last_entry_bar = ctx.bar_index
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vwap_deep_oversold_long",
                        "deviation_bps": round(deviation * 10000, 2),
                        "rsi": round(rsi, 2),
                        "vwap": round(vwap, 4),
                        "price": current_price,
                        "funding_rate": funding_rate,
                        "regime": regime,
                    },
                )

        # High-conviction Short Entry: Deep VWAP premium + confirmed overbought RSI
        if deviation > self.dev_threshold and rsi > self.rsi_overbought:
            if funding_rate > -0.0005:
                confidence = min(0.95, 0.70 + abs(deviation) * 15.0)
                self.last_entry_bar = ctx.bar_index
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vwap_deep_overbought_short",
                        "deviation_bps": round(deviation * 10000, 2),
                        "rsi": round(rsi, 2),
                        "vwap": round(vwap, 4),
                        "price": current_price,
                        "funding_rate": funding_rate,
                        "regime": regime,
                    },
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index