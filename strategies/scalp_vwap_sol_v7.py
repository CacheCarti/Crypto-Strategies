from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SolVwapMeanReversion(Strategy):
    METADATA = {
        "name": "SOL VWAP Deviation Reversion Scalp",
        "domain": "sol_usdc_scalp",
        "declared_sl_bps": 95.0,
        "declared_tp_bps": 160.0,
        "declared_hold_seconds": 1200,
        "warmup_bars": 35,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.vwap_period = 24
        self.rsi_period = 8
        self.dev_threshold_bps = 52.0  # 0.52% displacement from VWAP
        self.cooldown_bars = 14
        self.last_trade_bar = -999

    def _vwap(self, highs, lows, closes, volumes, period):
        if len(closes) < period:
            return None
        typical_prices = [(h + l + c) / 3.0 for h, l, c in zip(highs[-period:], lows[-period:], closes[-period:])]
        vols = volumes[-period:]
        total_pv = sum(tp * v for tp, v in zip(typical_prices, vols))
        total_v = sum(vols)
        if total_v <= 0.0:
            return None
        return total_pv / total_v

    def _rsi(self, closes, period):
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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars)
        highs = ctx.highs(self.warmup_bars)
        lows = ctx.lows(self.warmup_bars)
        volumes = ctx.volumes(self.warmup_bars)

        if len(closes) < self.warmup_bars:
            return None

        vwap = self._vwap(highs, lows, closes, volumes, self.vwap_period)
        rsi = self._rsi(closes, self.rsi_period)

        if vwap is None or rsi is None or vwap <= 0:
            return None

        current_price = ctx.bar.close
        dev_bps = ((current_price - vwap) / vwap) * 10000.0
        regime = ctx.market.get("regime", "NORMAL")

        # Position exit management on mean reversion touch
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            if pos_dir == "long" and current_price >= vwap:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "long_vwap_mean_reversion_target_hit",
                        "price": current_price,
                        "vwap": vwap,
                        "dev_bps": dev_bps,
                        "rsi": rsi,
                    },
                )
            elif pos_dir == "short" and current_price <= vwap:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "short_vwap_mean_reversion_target_hit",
                        "price": current_price,
                        "vwap": vwap,
                        "dev_bps": dev_bps,
                        "rsi": rsi,
                    },
                )
            return None

        # Cooldown guard to avoid clustered entries and friction drag
        if (ctx.bar_index - self.last_trade_bar) < self.cooldown_bars:
            return None

        # Filter out extreme stress regimes
        if regime in ["CRISIS", "MELTDOWN"]:
            return None

        # Entry logic: extreme deviation + oscillator confirmation
        if dev_bps <= -self.dev_threshold_bps and rsi < 32.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.55 + abs(dev_bps) / 200.0)
            return ctx.signal(
                "long",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_oversold_deviation_long",
                    "price": current_price,
                    "vwap": round(vwap, 3),
                    "dev_bps": round(dev_bps, 2),
                    "rsi": round(rsi, 2),
                    "regime": regime,
                },
            )

        if dev_bps >= self.dev_threshold_bps and rsi > 68.0:
            self.last_trade_bar = ctx.bar_index
            conf = min(0.9, 0.55 + abs(dev_bps) / 200.0)
            return ctx.signal(
                "short",
                confidence=conf,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "vwap_overbought_deviation_short",
                    "price": current_price,
                    "vwap": round(vwap, 3),
                    "dev_bps": round(dev_bps, 2),
                    "rsi": round(rsi, 2),
                    "regime": regime,
                },
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index