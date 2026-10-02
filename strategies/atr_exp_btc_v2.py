from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any, List
import math

class BtcVolExpansionMomentum(Strategy):
    METADATA = {
        "name": "BtcVolExpansionMomentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 580.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 65,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 10
        self.atr_base_period = 42
        self.expansion_threshold = 1.38
        self.contraction_threshold = 1.08
        self.momentum_bars = 8
        self.momentum_atr_mult = 1.25
        self.cooldown_bars = 5
        self.last_exit_bar = -999

    def _calc_tr_series(self, highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        return trs

    def _atr_and_baseline(self, highs: List[float], lows: List[float], closes: List[float]):
        req = self.atr_base_period + self.atr_period + 5
        if len(closes) < req:
            return None, None
        
        trs = self._calc_tr_series(highs, lows, closes)
        if len(trs) < self.atr_base_period + self.atr_period:
            return None, None

        # Rolling ATR values for baseline SMA
        atr_series = []
        for i in range(self.atr_period, len(trs) + 1):
            sub_trs = trs[i - self.atr_period:i]
            atr_series.append(sum(sub_trs) / self.atr_period)

        if len(atr_series) < self.atr_base_period:
            return None, None

        current_atr = atr_series[-1]
        atr_baseline = sum(atr_series[-self.atr_base_period:]) / self.atr_base_period
        return current_atr, atr_baseline

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        req_bars = self.atr_base_period + self.atr_period + self.momentum_bars + 10
        closes = ctx.closes(req_bars)
        highs = ctx.highs(req_bars)
        lows = ctx.lows(req_bars)

        if len(closes) < req_bars:
            return None

        current_atr, atr_baseline = self._atr_and_baseline(highs, lows, closes)
        if current_atr is None or atr_baseline is None or atr_baseline <= 0:
            return None

        atr_ratio = current_atr / atr_baseline
        price = closes[-1]

        # Manage open position: Volatility contraction exit
        if ctx.has_position():
            if atr_ratio < self.contraction_threshold:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.60,
                    metadata={
                        "reason": "volatility_contraction_exit",
                        "atr_ratio": round(atr_ratio, 3),
                        "current_atr": round(current_atr, 2),
                        "atr_baseline": round(atr_baseline, 2),
                        "price": round(price, 2)
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Market regime filter: avoid extreme meltdown
        regime = ctx.market.get("regime", "NORMAL")
        if regime in ("CRISIS", "MELTDOWN"):
            return None

        # Momentum calculation
        net_move = closes[-1] - closes[-1 - self.momentum_bars]
        min_move_req = current_atr * self.momentum_atr_mult

        # Volatility expansion entry condition
        if atr_ratio >= self.expansion_threshold:
            confidence = min(0.85, max(0.55, 0.55 + 0.20 * (atr_ratio - self.expansion_threshold)))

            # Bullish expansion
            if net_move > min_move_req:
                return ctx.signal(
                    "long",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bull_momentum",
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move": round(net_move, 2),
                        "current_atr": round(current_atr, 2),
                        "price": round(price, 2)
                    }
                )

            # Bearish expansion
            elif net_move < -min_move_req:
                return ctx.signal(
                    "short",
                    confidence=confidence,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    horizon_seconds=self.METADATA["declared_hold_seconds"],
                    metadata={
                        "reason": "vol_expansion_bear_momentum",
                        "atr_ratio": round(atr_ratio, 3),
                        "net_move": round(net_move, 2),
                        "current_atr": round(current_atr, 2),
                        "price": round(price, 2)
                    }
                )

        return None