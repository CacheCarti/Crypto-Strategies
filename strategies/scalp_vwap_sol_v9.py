from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVwapReversionScalp(Strategy):
    METADATA = {
        "name": "SolVwapReversionScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 110.0,
        "declared_hold_seconds": 1500,
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 36
        self.rsi_period = 14
        self.dev_threshold_bps = 95.0
        self.rsi_oversold = 26.0
        self.rsi_overbought = 74.0
        self.cooldown_bars = 36
        self.last_exit_bar = -100
        self.last_entry_bar = -100

    def _vwap(self, highs, lows, closes, volumes, period: int):
        if len(closes) < period:
            return None
        typical_prices = [(h + l + c) / 3.0 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
        vols = volumes[-period:]
        total_pv = sum(tp * v for tp, v in zip(typical_prices, vols))
        total_v = sum(vols)
        if total_v <= 0:
            return None
        return total_pv / total_v

    def _rsi(self, closes, period: int = 14):
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
        closes = ctx.closes(55)
        highs = ctx.highs(55)
        lows = ctx.lows(55)
        volumes = ctx.volumes(55)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.55:
            if ctx.has_position():
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.9,
                    metadata={
                        "reason": "crisis_exit",
                        "market_regime": market_regime,
                        "crisis_score": crisis_score,
                    },
                )
            return None

        current_price = ctx.bar.close
        open_price = ctx.bar.open
        vwap = self._vwap(highs, lows, closes, volumes, self.vwap_period)
        rsi = self._rsi(closes, self.rsi_period)

        if vwap is None or rsi is None or vwap == 0:
            return None

        dev_bps = ((current_price - vwap) / vwap) * 10000.0

        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_price >= vwap:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "long_vwap_reversion_target_hit",
                        "price": current_price,
                        "vwap": vwap,
                        "dev_bps": dev_bps,
                        "rsi": rsi,
                    },
                )
            elif direction == "short" and current_price <= vwap:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.8,
                    metadata={
                        "reason": "short_vwap_reversion_target_hit",
                        "price": current_price,
                        "vwap": vwap,
                        "dev_bps": dev_bps,
                        "rsi": rsi,
                    },
                )
            return None

        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None
        if (ctx.bar_index - self.last_entry_bar) < self.cooldown_bars:
            return None

        is_bullish_bar = current_price > open_price
        is_bearish_bar = current_price < open_price

        if dev_bps <= -self.dev_threshold_bps and rsi <= self.rsi_oversold and is_bullish_bar:
            stretch_factor = min(abs(dev_bps) / self.dev_threshold_bps, 2.0)
            confidence = min(0.65 + 0.15 * (stretch_factor - 1.0), 0.90)
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_oversold_reversal_entry",
                    "price": current_price,
                    "vwap": vwap,
                    "dev_bps": dev_bps,
                    "rsi": rsi,
                },
            )

        if dev_bps >= self.dev_threshold_bps and rsi >= self.rsi_overbought and is_bearish_bar:
            stretch_factor = min(dev_bps / self.dev_threshold_bps, 2.0)
            confidence = min(0.65 + 0.15 * (stretch_factor - 1.0), 0.90)
            self.last_entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_overbought_reversal_entry",
                    "price": current_price,
                    "vwap": vwap,
                    "dev_bps": dev_bps,
                    "rsi": rsi,
                },
            )

        return None