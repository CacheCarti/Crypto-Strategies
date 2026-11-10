from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthFundingReversion(Strategy):
    METADATA = {
        "name": "EthFundingReversion",
        "domain": "eth_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 450.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 72,
        "required_features": ["funding_rate_ethusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.funding_window = 72
        self.cooldown_period = 6
        self.last_exit_bar = -999
        self.consecutive_persisted = 3

    def _zscore(self, values: list) -> float:
        if len(values) < 20:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std = math.sqrt(variance)
        if std < 1e-9:
            return 0.0
        return (values[-1] - mean) / std

    def _rsi(self, closes: list, period: int = 14) -> Optional[float]:
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

    def _ema(self, values: list, period: int) -> Optional[float]:
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1.0 - k)
        return ema

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.funding_window + 1)
        if len(closes) < self.funding_window:
            return None

        # Build rolling funding history from ctx.features safely
        current_funding = ctx.features.get("funding_rate_ethusdt", 0.0)
        if not hasattr(self, "_funding_history"):
            self._funding_history = []
        self._funding_history.append(current_funding)
        if len(self._funding_history) > self.funding_window:
            self._funding_history.pop(0)

        if len(self._funding_history) < 30:
            return None

        fz = self._zscore(self._funding_history)
        rsi_val = self._rsi(closes, 14)
        ema_trend = self._ema(closes, 48)

        if rsi_val is None or ema_trend is None:
            return None

        curr_close = ctx.bar.close

        # Position management: exit on normalization
        if ctx.has_position():
            direction = ctx.position_direction()
            # If funding normalizes back near mean (|z| < 0.4), take profit / de-risk
            if abs(fz) < 0.4:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={"reason": "funding_normalized", "funding_z": round(fz, 2), "rsi": round(rsi_val, 1)}
                )
            # Stop counter-trend runaway
            if direction == "long" and rsi_val > 68:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_exhaustion_long", "rsi": round(rsi_val, 1)}
                )
            if direction == "short" and rsi_val < 32:
                return ctx.signal(
                    "flat",
                    confidence=0.7,
                    metadata={"reason": "rsi_exhaustion_short", "rsi": round(rsi_val, 1)}
                )
            return None

        # Cooldown guard to avoid overtrading
        if (ctx.bar_index - self.last_exit_bar) < self.cooldown_period:
            return None

        # Persistence check: require recent bars to also exhibit tension
        if len(self._funding_history) >= self.consecutive_persisted:
            recent = self._funding_history[-self.consecutive_persisted:]
            persist_neg = all(f < 0.0 for f in recent)
            persist_pos = all(f > 0.0 for f in recent)
        else:
            persist_neg = False
            persist_pos = False

        # Regime safety filter from market context
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Entry logic: Long squeeze when funding is deeply negative, but avoid fading hard breakdown
        # Price must not be catastrophically far below EMA (not in freefall), RSI showing stabilizing oversold
        if fz < -1.8 and persist_neg and (curr_close > ema_trend * 0.96) and (rsi_val < 42):
            confidence = min(0.85, 0.5 + abs(fz) * 0.1)
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "negative_funding_squeeze",
                    "funding_z": round(fz, 2),
                    "funding_raw": current_funding,
                    "rsi": round(rsi_val, 1),
                    "close": curr_close
                }
            )

        # Entry logic: Short fade when funding is deeply positive, avoid fading parabolic rally
        if fz > 1.8 and persist_pos and (curr_close < ema_trend * 1.04) and (rsi_val > 58):
            confidence = min(0.85, 0.5 + abs(fz) * 0.1)
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "positive_funding_fade",
                    "funding_z": round(fz, 2),
                    "funding_raw": current_funding,
                    "rsi": round(rsi_val, 1),
                    "close": curr_close
                }
            )

        return None