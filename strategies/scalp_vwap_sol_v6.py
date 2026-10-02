import math
from typing import Optional, Dict, Any
from domains.strategy_contract import Strategy, BarContext, Signal


class SolVwapReversionScalp(Strategy):
    METADATA = {
        "name": "SolVwapReversionScalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 140.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 2400,
        "warmup_bars": 40,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 30
        self.rsi_period = 9
        self.deviation_threshold = 0.0105  # 105 bps extreme stretch from VWAP
        self.rsi_oversold = 24.0
        self.rsi_overbought = 76.0
        self.cooldown_bars = 40  # Hard multi-bar cooldown (~3.3 hours)
        self.last_exit_bar = -999

    def _vwap(self, highs, lows, closes, volumes, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        typical_prices = [(h + l + c) / 3.0 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
        vols = volumes[-period:]
        total_pv = sum(tp * v for tp, v in zip(typical_prices, vols))
        total_v = sum(vols)
        if total_v <= 0:
            return None
        return total_pv / total_v

    def _rsi(self, closes, period: int) -> Optional[float]:
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
        closes = ctx.closes(self.vwap_period + 5)
        highs = ctx.highs(self.vwap_period + 5)
        lows = ctx.lows(self.vwap_period + 5)
        volumes = ctx.volumes(self.vwap_period + 5)

        if len(closes) < self.vwap_period + 5:
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        vwap = self._vwap(highs, lows, closes, volumes, self.vwap_period)
        rsi = self._rsi(closes, self.rsi_period)

        if vwap is None or rsi is None or vwap <= 0:
            return None

        dev_ratio = (current_close - vwap) / vwap
        dev_bps = dev_ratio * 10000.0

        # Position Management: Exit when reverting to VWAP midline
        if ctx.has_position():
            direction = ctx.position_direction()
            if direction == "long" and current_close >= vwap:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "vwap_target_reverted_long",
                        "price": current_close,
                        "vwap": round(vwap, 3),
                        "rsi": round(rsi, 2),
                        "dev_bps": round(dev_bps, 1),
                    },
                )
            elif direction == "short" and current_close <= vwap:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.85,
                    metadata={
                        "reason": "vwap_target_reverted_short",
                        "price": current_close,
                        "vwap": round(vwap, 3),
                        "rsi": round(rsi, 2),
                        "dev_bps": round(dev_bps, 1),
                    },
                )
            return None

        # Hard multi-bar cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime & Crisis Filter: Avoid choppy meltdown or volatile crisis states
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN", "HIGH_VOL") or crisis_score > 0.45:
            return None

        # Bullish Reversal: Price stretched far below VWAP + Deep RSI oversold + Reversal green candle
        if dev_ratio <= -self.deviation_threshold and rsi <= self.rsi_oversold and current_close > current_open:
            confidence = min(0.92, 0.70 + abs(dev_ratio) * 15.0)
            return ctx.signal(
                "long",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_oversold_candle_reversal_long",
                    "price": current_close,
                    "vwap": round(vwap, 3),
                    "rsi": round(rsi, 2),
                    "dev_bps": round(dev_bps, 1),
                    "regime": market_regime,
                },
            )

        # Bearish Reversal: Price stretched far above VWAP + Deep RSI overbought + Reversal red candle
        if dev_ratio >= self.deviation_threshold and rsi >= self.rsi_overbought and current_close < current_open:
            confidence = min(0.92, 0.70 + abs(dev_ratio) * 15.0)
            return ctx.signal(
                "short",
                confidence=confidence,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_overbought_candle_reversal_short",
                    "price": current_close,
                    "vwap": round(vwap, 3),
                    "rsi": round(rsi, 2),
                    "dev_bps": round(dev_bps, 1),
                    "regime": market_regime,
                },
            )

        return None