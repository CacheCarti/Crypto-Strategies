from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class SolVwapMeanReversionScalp(Strategy):
    METADATA = {
        "name": "SOL VWAP Mean Reversion Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 120.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 50,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 28
        self.rsi_period = 9
        self.dev_threshold_bps = 95.0  # 0.95% extreme stretch from rolling VWAP
        self.rsi_oversold = 24.0
        self.rsi_overbought = 76.0
        self.cooldown_bars = 42  # Hard 3.5-hour cooldown after every exit/action
        self.last_action_bar = -999

    def _vwap(self, highs, lows, closes, volumes, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        typical_prices = [(h + l + c) / 3.0 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
        vols = volumes[-period:]
        total_pv = sum(tp * v for tp, v in zip(typical_prices, vols))
        total_v = sum(vols)
        if total_v == 0:
            return None
        return total_pv / total_v

    def _rsi(self, closes, period: int = 9) -> Optional[float]:
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
        self.last_action_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.vwap_period + 5)
        opens = ctx.opens(self.vwap_period + 5)
        highs = ctx.highs(self.vwap_period + 5)
        lows = ctx.lows(self.vwap_period + 5)
        volumes = ctx.volumes(self.vwap_period + 5)

        if len(closes) < self.vwap_period + 1:
            return None

        current_price = ctx.bar.close
        current_open = ctx.bar.open
        vwap = self._vwap(highs, lows, closes, volumes, self.vwap_period)
        rsi = self._rsi(closes, self.rsi_period)

        if vwap is None or rsi is None or vwap == 0:
            return None

        dev_bps = ((current_price - vwap) / vwap) * 10000.0
        direction = ctx.position_direction()

        # Dynamic exit: Close position upon crossing back to the VWAP baseline
        if direction == "long" and current_price >= vwap:
            self.last_action_bar = ctx.bar_index
            return ctx.signal(
                "flat",
                confidence=0.85,
                metadata={
                    "reason": "vwap_mean_reversion_long_target_reached",
                    "price": current_price,
                    "vwap": vwap,
                    "rsi": rsi,
                    "dev_bps": dev_bps,
                },
            )

        if direction == "short" and current_price <= vwap:
            self.last_action_bar = ctx.bar_index
            return ctx.signal(
                "flat",
                confidence=0.85,
                metadata={
                    "reason": "vwap_mean_reversion_short_target_reached",
                    "price": current_price,
                    "vwap": vwap,
                    "rsi": rsi,
                    "dev_bps": dev_bps,
                },
            )

        # Gate out if already in position or still within post-trade cooldown
        if ctx.has_position():
            return None

        if (ctx.bar_index - self.last_action_bar) < self.cooldown_bars:
            return None

        # Filter out volatile/crisis market environments to prevent catching falling knives
        crisis_score = ctx.market.get("crisis_score", 0.0)
        market_regime = ctx.market.get("regime", "NORMAL")
        if crisis_score > 0.35 or market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL"):
            return None

        # Long Setup: Extreme negative deviation + deep oversold RSI + reversal candle confirmation
        if dev_bps <= -self.dev_threshold_bps and rsi <= self.rsi_oversold and current_price > current_open:
            self.last_action_bar = ctx.bar_index
            confidence = min(0.95, 0.70 + (abs(dev_bps) - self.dev_threshold_bps) / 200.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_extreme_oversold_reversal",
                    "price": current_price,
                    "vwap": vwap,
                    "rsi": rsi,
                    "dev_bps": dev_bps,
                    "crisis_score": crisis_score,
                },
            )

        # Short Setup: Extreme positive deviation + deep overbought RSI + reversal candle confirmation
        if dev_bps >= self.dev_threshold_bps and rsi >= self.rsi_overbought and current_price < current_open:
            self.last_action_bar = ctx.bar_index
            confidence = min(0.95, 0.70 + (dev_bps - self.dev_threshold_bps) / 200.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_extreme_overbought_reversal",
                    "price": current_price,
                    "vwap": vwap,
                    "rsi": rsi,
                    "dev_bps": dev_bps,
                    "crisis_score": crisis_score,
                },
            )

        return None