from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math


class FundingTiltedRsiScalp(Strategy):
    METADATA = {
        "name": "Funding Tilted RSI Scalp",
        "domain": "eth_usdc_scalp",
        "declared_sl_bps": 100.0,
        "declared_tp_bps": 180.0,
        "declared_hold_seconds": 600,
        "warmup_bars": 40,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 7
        self.rsi_oversold = 20.0
        self.rsi_overbought = 80.0
        self.rsi_exit_long = 50.0
        self.rsi_exit_short = 50.0

        # Stringent funding threshold to only trade genuine crowd crowding
        self.funding_threshold = 0.00005
        
        # Hard multi-bar cooldown to prevent churn and friction bleed
        self.cooldown_bars = 48
        self.max_hold_bars = 10
        
        self.last_exit_bar = -200
        self.entry_bar = -200

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

    def _bollinger_bands(self, closes, period: int = 20, num_std: float = 2.0):
        if len(closes) < period:
            return None, None, None
        slice_c = closes[-period:]
        mean = sum(slice_c) / period
        variance = sum((x - mean) ** 2 for x in slice_c) / period
        std = math.sqrt(variance)
        return mean, mean + num_std * std, mean - num_std * std

    def _atr_bps(self, highs, lows, closes, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 80.0
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        atr_val = sum(trs[-period:]) / period
        current_close = closes[-1]
        if current_close <= 0:
            return 80.0
        return (atr_val / current_close) * 10000.0

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        warmup = self.METADATA["warmup_bars"]
        closes = ctx.closes(warmup)
        if len(closes) < warmup:
            return None

        highs = ctx.highs(warmup)
        lows = ctx.lows(warmup)
        
        rsi = self._rsi(closes, self.rsi_period)
        if rsi is None:
            return None

        _, bb_upper, bb_lower = self._bollinger_bands(closes, 20, 2.0)
        if bb_upper is None or bb_lower is None:
            return None

        funding_rate = ctx.features.get("funding_rate_ethusdt", 0.0)
        current_price = ctx.bar.close
        atr_bps = self._atr_bps(highs, lows, closes, 14)

        # 1. Manage Active Position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            should_exit = False
            exit_reason = ""
            
            if pos_dir == "long":
                if rsi >= self.rsi_exit_long:
                    should_exit = True
                    exit_reason = "rsi_normalized_long"
                elif bars_held >= self.max_hold_bars:
                    should_exit = True
                    exit_reason = "max_hold_timeout_long"
            elif pos_dir == "short":
                if rsi <= self.rsi_exit_short:
                    should_exit = True
                    exit_reason = "rsi_normalized_short"
                elif bars_held >= self.max_hold_bars:
                    should_exit = True
                    exit_reason = "max_hold_timeout_short"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "rsi": round(rsi, 2),
                        "funding_rate": funding_rate,
                        "bars_held": bars_held,
                        "price": current_price,
                    },
                )
            return None

        # 2. Strict Cooldown Gate
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_bars:
            return None

        # 3. Market Regime Filter (Avoid high stress / meltdown)
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # 4. Dynamic Risk Envelope
        dyn_sl = max(70.0, min(130.0, atr_bps * 1.3))
        dyn_tp = max(120.0, min(240.0, atr_bps * 2.2))

        # 5. Strict Entry Logic
        # Long condition: Funding heavily negative + Deep RSI oversold + Price touching lower band
        if funding_rate <= -self.funding_threshold and rsi <= self.rsi_oversold and current_price <= bb_lower:
            self.entry_bar = ctx.bar_index
            funding_intensity = min(1.0, abs(funding_rate) / 0.0002)
            confidence = 0.60 + 0.30 * funding_intensity
            
            return ctx.signal(
                "long",
                confidence=min(0.95, confidence),
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "negative_funding_deep_rsi_bb_oversold",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding_rate,
                    "atr_bps": round(atr_bps, 2),
                    "bb_lower": round(bb_lower, 2),
                    "price": current_price,
                    "regime": regime,
                },
            )

        # Short condition: Funding heavily positive + High RSI overbought + Price touching upper band
        if funding_rate >= self.funding_threshold and rsi >= self.rsi_overbought and current_price >= bb_upper:
            self.entry_bar = ctx.bar_index
            funding_intensity = min(1.0, abs(funding_rate) / 0.0002)
            confidence = 0.60 + 0.30 * funding_intensity
            
            return ctx.signal(
                "short",
                confidence=min(0.95, confidence),
                stop_loss_bps=dyn_sl,
                take_profit_bps=dyn_tp,
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "positive_funding_high_rsi_bb_overbought",
                    "rsi": round(rsi, 2),
                    "funding_rate": funding_rate,
                    "atr_bps": round(atr_bps, 2),
                    "bb_upper": round(bb_upper, 2),
                    "price": current_price,
                    "regime": regime,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index