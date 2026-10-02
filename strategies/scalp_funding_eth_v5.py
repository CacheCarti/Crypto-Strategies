from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingTiltedScalper(Strategy):
    METADATA = {
        "name": "Funding Tilted Scalper",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 130.0,
        "declared_tp_bps": 220.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 10
        self.bb_period = 20
        self.bb_std = 2.0
        self.rsi_oversold = 20.0
        self.rsi_overbought = 80.0
        self.rsi_exit_long = 50.0
        self.rsi_exit_short = 50.0
        self.min_funding_long = -0.00005  # Shorts paying longs with meaningful magnitude
        self.min_funding_short = 0.00012  # Longs crowded and paying elevated funding
        self.cooldown_bars = 36          # 3 hours minimum cooldown (36 * 5m bars)
        self.last_exit_bar = -999

    def _rsi(self, closes: list, period: int) -> Optional[float]:
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

    def _bollinger(self, closes: list, period: int, num_std: float):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.METADATA["warmup_bars"])
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Filter out meltdown / extreme crisis
        if ctx.regime == "crisis":
            return None

        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        bb_mid, bb_upper, bb_lower = self._bollinger(closes, self.bb_period, self.bb_std)
        if bb_upper is None or bb_lower is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_price = ctx.bar.close

        # Position management / RSI normalization exit
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and rsi >= self.rsi_exit_long:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_rsi_normalized",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "price": current_price,
                        "bb_mid": round(bb_mid, 2),
                    },
                )
            elif pos_dir == "short" and rsi <= self.rsi_exit_short:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_rsi_normalized",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "price": current_price,
                        "bb_mid": round(bb_mid, 2),
                    },
                )
            return None

        # Hard multi-bar cooldown guard after previous trade
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # Long Entry: Negative funding skew + Deep RSI oversold + Lower Bollinger Band penetration
        if funding <= self.min_funding_long and rsi <= self.rsi_oversold and current_price <= bb_lower:
            self.last_exit_bar = ctx.bar_index
            conf = min(0.95, 0.60 + abs(funding) * 800.0 + (self.rsi_oversold - rsi) * 0.02)
            return ctx.signal(
                "long",
                confidence=round(conf, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "negative_funding_oversold_bb_bounce",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "price": current_price,
                    "bb_lower": round(bb_lower, 2),
                },
            )

        # Short Entry: Elevated positive funding + Deep RSI overbought + Upper Bollinger Band penetration
        if funding >= self.min_funding_short and rsi >= self.rsi_overbought and current_price >= bb_upper:
            self.last_exit_bar = ctx.bar_index
            conf = min(0.95, 0.60 + funding * 800.0 + (rsi - self.rsi_overbought) * 0.02)
            return ctx.signal(
                "short",
                confidence=round(conf, 3),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "positive_funding_overbought_bb_fade",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding,
                    "price": current_price,
                    "bb_upper": round(bb_upper, 2),
                },
            )

        return None