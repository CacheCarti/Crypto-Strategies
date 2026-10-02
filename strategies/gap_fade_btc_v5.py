from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcWickRejectionReversion(Strategy):
    METADATA = {
        "name": "BTC Wick Rejection Reversion",
        "domain": "btc_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 340.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 30,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.vol_period = 14
        self.rsi_period = 14
        self.wick_ratio_threshold = 0.48
        self.range_mult = 0.90
        self.vol_mult = 0.90
        self.cooldown_bars = 4
        self.max_hold_bars = 8
        self.last_trade_bar = -100
        self.entry_bar_idx = -100
        self.wick_target_price = 0.0

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

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(40)
        highs = ctx.highs(40)
        lows = ctx.lows(40)
        volumes = ctx.volumes(40)

        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Market regime safety exit
        market_regime = ctx.market.get("regime", "NORMAL")
        if market_regime in ("CRISIS", "MELTDOWN"):
            if ctx.has_position():
                return ctx.signal("flat", confidence=0.8, metadata={"reason": "exit_crisis_regime", "regime": market_regime})
            return None

        current_close = ctx.bar.close
        current_open = ctx.bar.open
        current_high = ctx.bar.high
        current_low = ctx.bar.low
        current_vol = ctx.bar.volume

        # Manage existing position
        if ctx.has_position():
            bars_held = ctx.bar_index - self.entry_bar_idx
            direction = ctx.position_direction()

            # Time-based hold expiration
            if bars_held >= self.max_hold_bars:
                return ctx.signal("flat", confidence=0.5, metadata={
                    "reason": "max_bars_held_reached",
                    "bars_held": bars_held,
                    "close": current_close
                })

            # Wick fill target reached
            if direction == "long" and self.wick_target_price > 0 and current_high >= self.wick_target_price:
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "wick_reversion_target_hit_long",
                    "target": self.wick_target_price,
                    "high": current_high
                })
            elif direction == "short" and self.wick_target_price > 0 and current_low <= self.wick_target_price:
                return ctx.signal("flat", confidence=0.65, metadata={
                    "reason": "wick_reversion_target_hit_short",
                    "target": self.wick_target_price,
                    "low": current_low
                })

            return None

        # Cooldown guard
        if ctx.bar_index - self.last_trade_bar < self.cooldown_bars:
            return None

        atr = self._atr(highs, lows, closes, self.atr_period)
        avg_vol = self._sma(volumes, self.vol_period)
        rsi = self._rsi(closes, self.rsi_period)

        if atr is None or avg_vol is None or rsi is None or atr <= 0 or avg_vol <= 0:
            return None

        bar_range = current_high - current_low
        if bar_range <= 0:
            return None

        body_top = max(current_open, current_close)
        body_bottom = min(current_open, current_close)
        upper_wick = current_high - body_top
        lower_wick = body_bottom - current_low

        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range
        range_to_atr = bar_range / atr
        vol_ratio = current_vol / avg_vol

        # Loosened range and volume filters to allow valid wick setups to fire
        if range_to_atr < self.range_mult or vol_ratio < self.vol_mult:
            return None

        # Long Entry: Lower wick rejection of lows (buyers stepped in)
        if lower_wick_ratio >= self.wick_ratio_threshold and rsi < 65.0:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar_idx = ctx.bar_index
            self.wick_target_price = current_high
            conf = min(0.85, 0.50 + 0.30 * (lower_wick_ratio - 0.45) + 0.10 * min(max(vol_ratio - 1.0, 0.0), 1.0))
            return ctx.signal("long", confidence=round(conf, 2), metadata={
                "reason": "lower_wick_rejection_long",
                "lower_wick_ratio": round(lower_wick_ratio, 3),
                "range_to_atr": round(range_to_atr, 2),
                "vol_ratio": round(vol_ratio, 2),
                "rsi": round(rsi, 1),
                "price": current_close
            })

        # Short Entry: Upper wick rejection of highs (sellers stepped in)
        if upper_wick_ratio >= self.wick_ratio_threshold and rsi > 35.0:
            self.last_trade_bar = ctx.bar_index
            self.entry_bar_idx = ctx.bar_index
            self.wick_target_price = current_low
            conf = min(0.85, 0.50 + 0.30 * (upper_wick_ratio - 0.45) + 0.10 * min(max(vol_ratio - 1.0, 0.0), 1.0))
            return ctx.signal("short", confidence=round(conf, 2), metadata={
                "reason": "upper_wick_rejection_short",
                "upper_wick_ratio": round(upper_wick_ratio, 3),
                "range_to_atr": round(range_to_atr, 2),
                "vol_ratio": round(vol_ratio, 2),
                "rsi": round(rsi, 1),
                "price": current_close
            })

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_trade_bar = ctx.bar_index
        self.wick_target_price = 0.0