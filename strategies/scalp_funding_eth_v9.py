from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class FundingTiltedScalp(Strategy):
    METADATA = {
        "name": "FundingTiltedScalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 140.0,
        "declared_tp_bps": 280.0,
        "declared_hold_seconds": 1800,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 9
        self.oversold_thresh = 20.0
        self.overbought_thresh = 80.0
        self.funding_long_thresh = -0.00003
        self.funding_short_thresh = 0.00015
        self.cooldown_bars = 48
        self.last_trade_bar = -100

    def _rsi(self, closes: list, period: int) -> Optional[float]:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
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

    def _bollinger(self, closes: list, period: int = 20, num_std: float = 2.2):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(50)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        current_price = ctx.bar.close
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        mid, upper_bb, lower_bb = self._bollinger(closes, period=20, num_std=2.2)
        if mid is None or upper_bb is None or lower_bb is None:
            return None

        funding = ctx.features.get("funding_rate_ethusdt", 0.0)

        # Active position management
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            
            # Mean-reversion exit targets
            if pos_dir == "long" and (rsi >= 50.0 or current_price >= mid):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "long_mean_reversion_target_hit",
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "mid_bb": round(mid, 2),
                        "funding": funding
                    }
                )
            elif pos_dir == "short" and (rsi <= 50.0 or current_price <= mid):
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={
                        "reason": "short_mean_reversion_target_hit",
                        "rsi": round(rsi, 2),
                        "price": current_price,
                        "mid_bb": round(mid, 2),
                        "funding": funding
                    }
                )
            return None

        # Mandatory multi-bar cooldown
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Regime safety filter: avoid extreme breakdown conditions
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        is_bullish_bar = ctx.bar.close > ctx.bar.open
        is_bearish_bar = ctx.bar.close < ctx.bar.open

        # Long Setup: Meaningfully negative funding (crowded shorts) + Deep RSI Oversold + Sub-BB Dip with Reversal
        if funding <= self.funding_long_thresh and rsi <= self.oversold_thresh:
            if current_price <= lower_bb and is_bullish_bar:
                self.last_trade_bar = ctx.bar_index
                confidence = min(0.9, 0.65 + abs(funding) * 2000.0 + (self.oversold_thresh - rsi) * 0.01)
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "negative_funding_extreme_oversold_bounce",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "price": current_price,
                        "lower_bb": round(lower_bb, 2)
                    }
                )

        # Short Setup: Substantially positive funding (crowded longs) + Deep RSI Overbought + Upper-BB Spike with Rejection
        if funding >= self.funding_short_thresh and rsi >= self.overbought_thresh:
            if current_price >= upper_bb and is_bearish_bar:
                self.last_trade_bar = ctx.bar_index
                confidence = min(0.9, 0.65 + abs(funding) * 2000.0 + (rsi - self.overbought_thresh) * 0.01)
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "high_funding_extreme_overbought_rejection",
                        "rsi": round(rsi, 2),
                        "funding_rate": funding,
                        "price": current_price,
                        "upper_bb": round(upper_bb, 2)
                    }
                )

        return None