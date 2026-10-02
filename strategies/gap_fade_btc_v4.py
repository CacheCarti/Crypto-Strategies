from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class BtcWickReversion(Strategy):
    METADATA = {
        "name": "BtcWickReversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 35,
        "required_features": ["fear_greed_index"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.vol_period = 20
        self.rsi_period = 14
        self.min_wick_ratio = 0.65
        self.min_range_atr_mult = 1.50
        self.min_vol_mult = 1.45
        self.cooldown_bars = 8
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _atr(self, highs, lows, closes, period: int = 14) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _sma(self, values, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

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
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        n_bars = ctx.bar_index
        if n_bars < self.METADATA["warmup_bars"]:
            return None

        closes = ctx.closes(35)
        highs = ctx.highs(35)
        lows = ctx.lows(35)
        volumes = ctx.volumes(35)

        if len(closes) < 35 or len(highs) < 35 or len(lows) < 35 or len(volumes) < 35:
            return None

        current_high = highs[-1]
        current_low = lows[-1]
        current_close = closes[-1]
        current_open = ctx.opens(1)[-1]
        current_vol = volumes[-1]

        atr = self._atr(highs, lows, closes, self.atr_period)
        avg_vol = self._sma(volumes, self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or avg_vol is None or rsi is None or atr <= 0.0 or avg_vol <= 0.0:
            return None

        total_range = current_high - current_low
        if total_range <= 0.0:
            return None

        upper_wick = current_high - max(current_open, current_close)
        lower_wick = min(current_open, current_close) - current_low

        upper_wick_ratio = upper_wick / total_range
        lower_wick_ratio = lower_wick / total_range
        range_mult = total_range / atr
        vol_mult = current_vol / avg_vol

        # Regime safety filter
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("MELTDOWN", "CRISIS"):
            return None

        # Position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar

            # Fade strong opposing wick rejection while in trade
            if pos_dir == "long" and upper_wick_ratio >= 0.68 and vol_mult > 1.5:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "exit_opposing_upper_rejection",
                        "upper_wick_ratio": round(upper_wick_ratio, 3),
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                    },
                )
            elif pos_dir == "short" and lower_wick_ratio >= 0.68 and vol_mult > 1.5:
                return ctx.signal(
                    "flat",
                    confidence=0.65,
                    metadata={
                        "reason": "exit_opposing_lower_rejection",
                        "lower_wick_ratio": round(lower_wick_ratio, 3),
                        "bars_held": bars_held,
                        "rsi": round(rsi, 2),
                    },
                )
            return None

        # Hard multi-bar cooldown after exit
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Tight volatility & volume expansion gate
        if range_mult < self.min_range_atr_mult or vol_mult < self.min_vol_mult:
            return None

        fear_greed = ctx.features.get("fear_greed_index", 50)

        # Bullish Rejection of Lows -> Long (Wick must reject lows in non-overbought zone)
        if lower_wick_ratio >= self.min_wick_ratio and 25.0 <= rsi <= 55.0:
            conf = min(0.85, 0.60 + (lower_wick_ratio - self.min_wick_ratio) * 0.5 + min(0.15, (vol_mult - 1.45) * 0.1))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(conf, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "lower_wick_exhaustion_long",
                    "lower_wick_ratio": round(lower_wick_ratio, 3),
                    "range_to_atr": round(range_mult, 2),
                    "vol_to_avg": round(vol_mult, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                },
            )

        # Bearish Rejection of Highs -> Short (Wick must reject highs in non-oversold zone)
        if upper_wick_ratio >= self.min_wick_ratio and 45.0 <= rsi <= 75.0:
            conf = min(0.85, 0.60 + (upper_wick_ratio - self.min_wick_ratio) * 0.5 + min(0.15, (vol_mult - 1.45) * 0.1))
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(conf, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                metadata={
                    "reason": "upper_wick_exhaustion_short",
                    "upper_wick_ratio": round(upper_wick_ratio, 3),
                    "range_to_atr": round(range_mult, 2),
                    "vol_to_avg": round(vol_mult, 2),
                    "rsi": round(rsi, 2),
                    "fear_greed": fear_greed,
                },
            )

        return None