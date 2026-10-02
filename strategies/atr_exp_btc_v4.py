from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class VolatilityExpansionMomentum(Strategy):
    METADATA = {
        "name": "BTC Volatility Expansion Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 320.0,
        "declared_tp_bps": 650.0,
        "declared_hold_seconds": 28800,
        "warmup_bars": 70,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 14
        self.atr_baseline_period = 48
        self.momentum_bars = 12
        self.expansion_threshold = 1.55
        self.momentum_atr_multiple = 1.85
        self.cooldown_bars = 14
        self.last_exit_bar = -100

    def _compute_atr_series(self, highs, lows, closes, period: int):
        if len(closes) < period + 1:
            return []
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        
        atrs = []
        for i in range(period, len(trs) + 1):
            window_tr = trs[i - period:i]
            atrs.append(sum(window_tr) / period)
        return atrs

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.atr_baseline_period + self.atr_period + self.momentum_bars + 10
        closes = ctx.closes(req_bars)
        if len(closes) < req_bars:
            return None

        # Filter out extreme crisis regimes where breakouts are prone to high slippage / wicks
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)

        atr_series = self._compute_atr_series(highs, lows, closes, self.atr_period)
        if len(atr_series) < self.atr_baseline_period:
            return None

        current_atr = atr_series[-1]
        baseline_slice = atr_series[-self.atr_baseline_period:]
        avg_atr = sum(baseline_slice) / len(baseline_slice)

        if avg_atr <= 0.0 or current_atr <= 0.0:
            return None

        atr_ratio = current_atr / avg_atr
        current_close = closes[-1]
        past_close = closes[-1 - self.momentum_bars]
        net_move = current_close - past_close
        net_move_atr_ratio = abs(net_move) / current_atr

        has_pos = ctx.has_position()
        pos_dir = ctx.position_direction()

        # Exit logic: Exit only when volatility contracts substantially back below normal
        if has_pos:
            if atr_ratio < 0.88:
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "volatility_contracted_sub_baseline",
                        "atr_ratio": round(atr_ratio, 3),
                        "current_atr": round(current_atr, 2),
                        "avg_atr": round(avg_atr, 2),
                        "net_move": round(net_move, 2),
                        "price": round(current_close, 2),
                    }
                )
            return None

        # Mandatory multi-bar post-exit cooldown to prevent overtrading
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Tightened expansion & clear directional momentum criteria
        is_expanding = atr_ratio >= self.expansion_threshold
        has_strong_momentum = net_move_atr_ratio >= self.momentum_atr_multiple

        if is_expanding and has_strong_momentum:
            confidence = min(0.95, 0.55 + (atr_ratio - self.expansion_threshold) * 0.25 + (net_move_atr_ratio - self.momentum_atr_multiple) * 0.1)

            if net_move > 0:
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bullish_volatility_expansion_breakout",
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move_atr_ratio": round(net_move_atr_ratio, 2),
                        "current_atr": round(current_atr, 2),
                        "avg_atr": round(avg_atr, 2),
                        "net_move": round(net_move, 2),
                        "price": round(current_close, 2),
                    }
                )
            elif net_move < 0:
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "bearish_volatility_expansion_breakout",
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move_atr_ratio": round(net_move_atr_ratio, 2),
                        "current_atr": round(current_atr, 2),
                        "avg_atr": round(avg_atr, 2),
                        "net_move": round(net_move, 2),
                        "price": round(current_close, 2),
                    }
                )

        return None