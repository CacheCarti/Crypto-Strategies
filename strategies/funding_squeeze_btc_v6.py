from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcFundingSqueeze(Strategy):
    METADATA = {
        "name": "BTC Funding Squeeze Divergence",
        "domain": "btc_usdc",
        "declared_sl_bps": 260.0,
        "declared_tp_bps": 520.0,
        "declared_hold_seconds": 14400,  # 4 hours
        "warmup_bars": 30,
        "required_features": ["funding_rate_btcusdt"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.lookback = 12
        self.cooldown_bars = 4
        self.last_signal_bar = -100
        self.entry_bar = -1

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0.0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_signal_bar = ctx.bar_index
        self.entry_bar = -1

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + 20)
        if len(closes) < self.lookback + 15:
            return None

        current_price = closes[-1]
        base_price = closes[-self.lookback]
        if base_price <= 0:
            return None

        price_return_pct = (current_price - base_price) / base_price
        funding_rate = ctx.features.get("funding_rate_btcusdt", 0.0)
        rsi_val = self._rsi(closes, period=14) or 50.0

        # Market regime filter - bypass crisis trading
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.7, metadata={"reason": "risk_off_crisis_exit", "regime": market_regime})
            return None

        # Position management & dynamic exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar if self.entry_bar > 0 else 0

            if pos_dir == "long":
                if funding_rate > 0.0003 or rsi_val > 76.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "long_exhaustion_or_overheated_funding",
                            "funding_rate": funding_rate,
                            "rsi": round(rsi_val, 2),
                            "bars_held": bars_held
                        }
                    )
            elif pos_dir == "short":
                if funding_rate < -0.0001 or rsi_val < 24.0:
                    return ctx.signal(
                        "flat",
                        confidence=0.6,
                        metadata={
                            "reason": "short_exhaustion_or_negative_funding",
                            "funding_rate": funding_rate,
                            "rsi": round(rsi_val, 2),
                            "bars_held": bars_held
                        }
                    )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_signal_bar < self.cooldown_bars:
            return None

        # Baseline crypto funding rate sits around +0.0001 (+0.01% / 8h).
        # Bullish Squeeze: Price rising (> +0.45%) while funding is depressed / negative (<= 0.00004).
        # Trapped shorts are forced to cover into upward momentum.
        if price_return_pct > 0.0045 and funding_rate <= 0.00004 and rsi_val < 74.0:
            confidence = min(0.85, 0.55 + max(0.0, -funding_rate) * 2000 + price_return_pct * 4)
            self.last_signal_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "short_trap_bullish_squeeze",
                    "price_return_pct": round(price_return_pct, 4),
                    "funding_rate": funding_rate,
                    "rsi": round(rsi_val, 2),
                    "price": current_price
                }
            )

        # Bearish Squeeze: Price falling (< -0.45%) while funding remains high / crowded long (>= 0.00008).
        # Trapped longs face cascading liquidation pressures.
        if price_return_pct < -0.0045 and funding_rate >= 0.00008 and rsi_val > 26.0:
            confidence = min(0.85, 0.55 + funding_rate * 1200 + abs(price_return_pct) * 4)
            self.last_signal_bar = ctx.bar_index
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=round(confidence, 2),
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "long_trap_bearish_squeeze",
                    "price_return_pct": round(price_return_pct, 4),
                    "funding_rate": funding_rate,
                    "rsi": round(rsi_val, 2),
                    "price": current_price
                }
            )

        return None