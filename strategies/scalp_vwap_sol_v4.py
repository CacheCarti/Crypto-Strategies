from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolVwapReversionScalp(Strategy):
    METADATA = {
        "name": "SOL VWAP Mean Reversion Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 100.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 30
        self.rsi_period = 14
        self.min_deviation_bps = 95.0
        self.cooldown_bars = 40
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _vwap(self, highs, lows, closes, volumes, period: int) -> Optional[float]:
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

    def _rsi(self, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = self.METADATA["warmup_bars"]
        closes = ctx.closes(warmup)
        if len(closes) < warmup:
            return None

        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)
        volumes = ctx.volumes(warmup)
        opens = ctx.opens(warmup)

        vwap_val = self._vwap(highs, lows, closes, volumes, self.vwap_period)
        rsi_val = self._rsi(closes, self.rsi_period)

        if vwap_val is None or rsi_val is None or vwap_val <= 0:
            return None

        current_price = closes[-1]
        current_open = opens[-1]
        dev_bps = ((current_price - vwap_val) / vwap_val) * 10000.0
        regime = ctx.market.get("regime", "NORMAL")

        # Position Management: Exit on mean reversion touch
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_price >= vwap_val:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_vwap_mean_reverted",
                        "price": current_price,
                        "vwap": round(vwap_val, 4),
                        "dev_bps": round(dev_bps, 2),
                        "rsi": round(rsi_val, 2),
                    },
                )
            elif pos_dir == "short" and current_price <= vwap_val:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_vwap_mean_reverted",
                        "price": current_price,
                        "vwap": round(vwap_val, 4),
                        "dev_bps": round(dev_bps, 2),
                        "rsi": round(rsi_val, 2),
                    },
                )
            return None

        # Hard Cooldown: Enforce spacing between trades to eliminate friction bleed
        bars_since_exit = ctx.bar_index - self.last_exit_bar
        bars_since_entry = ctx.bar_index - self.last_entry_bar
        if bars_since_exit < self.cooldown_bars or bars_since_entry < self.cooldown_bars:
            return None

        # Filter out hostile market regimes
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # Long: Extreme oversold stretch below VWAP + confirmation bounce candle
        if dev_bps <= -self.min_deviation_bps and rsi_val <= 26.0 and current_price > current_open:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.95, 0.65 + abs(dev_bps) / 400.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_deep_oversold_bounce",
                    "price": current_price,
                    "vwap": round(vwap_val, 4),
                    "dev_bps": round(dev_bps, 2),
                    "rsi": round(rsi_val, 2),
                    "regime": regime,
                },
            )

        # Short: Extreme overbought stretch above VWAP + confirmation rejection candle
        if dev_bps >= self.min_deviation_bps and rsi_val >= 74.0 and current_price < current_open:
            self.last_entry_bar = ctx.bar_index
            confidence = min(0.95, 0.65 + abs(dev_bps) / 400.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_deep_overbought_rejection",
                    "price": current_price,
                    "vwap": round(vwap_val, 4),
                    "dev_bps": round(dev_bps, 2),
                    "rsi": round(rsi_val, 2),
                    "regime": regime,
                },
            )

        return None