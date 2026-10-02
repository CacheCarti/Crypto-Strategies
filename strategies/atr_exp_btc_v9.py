from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class BtcVolatilityExpansionMomentum(Strategy):
    METADATA = {
        "name": "BTC Volatility Expansion Momentum",
        "domain": "btc_usdc",
        "declared_sl_bps": 280.0,
        "declared_tp_bps": 620.0,
        "declared_hold_seconds": 18000,
        "warmup_bars": 65,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.atr_period = 16
        self.atr_ma_period = 40
        self.momentum_bars = 10
        self.expansion_ratio = 1.35
        self.contraction_ratio = 1.05
        self.move_atr_mult = 1.60
        self.cooldown_bars = 8
        self.last_exit_bar = -999

    def _calc_atr_and_baseline(self, highs, lows, closes):
        needed = self.atr_period + self.atr_ma_period
        if len(closes) < needed + 1:
            return None, None
        
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1])
            )
            trs.append(tr)
            
        if len(trs) < needed:
            return None, None
            
        atr_series = []
        for offset in range(self.atr_ma_period):
            end_idx = len(trs) - self.atr_ma_period + 1 + offset
            start_idx = end_idx - self.atr_period
            atr_series.append(sum(trs[start_idx:end_idx]) / self.atr_period)
            
        current_atr = atr_series[-1]
        baseline_atr = sum(atr_series) / len(atr_series)
        return current_atr, baseline_atr

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        total_history = self.atr_period + self.atr_ma_period + self.momentum_bars + 5
        if ctx.bar_index < total_history:
            return None

        closes = ctx.closes(total_history)
        highs = ctx.highs(total_history)
        lows = ctx.lows(total_history)

        current_atr, baseline_atr = self._calc_atr_and_baseline(highs, lows, closes)
        if current_atr is None or baseline_atr is None or baseline_atr <= 0:
            return None

        atr_ratio = current_atr / baseline_atr
        current_close = closes[-1]
        past_close = closes[-1 - self.momentum_bars]
        net_move = current_close - past_close
        move_threshold = self.move_atr_mult * current_atr

        # Manage open position
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            should_exit = False
            exit_reason = ""

            if atr_ratio < self.contraction_ratio:
                should_exit = True
                exit_reason = "atr_contracted_below_baseline"
            elif pos_dir == "long" and net_move < -0.5 * move_threshold:
                should_exit = True
                exit_reason = "momentum_reversal_against_long"
            elif pos_dir == "short" and net_move > 0.5 * move_threshold:
                should_exit = True
                exit_reason = "momentum_reversal_against_short"

            if should_exit:
                self.last_exit_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": exit_reason,
                        "atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "atr_ratio": round(atr_ratio, 2),
                        "net_move": round(net_move, 2),
                    }
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Volatility expansion entry logic
        if atr_ratio >= self.expansion_ratio:
            if net_move >= move_threshold:
                conf = min(0.85, 0.55 + 0.15 * (atr_ratio - self.expansion_ratio) + 0.15 * (net_move / move_threshold - 1.0))
                return ctx.signal(
                    "long",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "vol_expansion_bullish_breakout",
                        "atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "atr_ratio": round(atr_ratio, 2),
                        "net_move": round(net_move, 2),
                        "threshold": round(move_threshold, 2),
                    }
                )
            elif net_move <= -move_threshold:
                conf = min(0.85, 0.55 + 0.15 * (atr_ratio - self.expansion_ratio) + 0.15 * (abs(net_move) / move_threshold - 1.0))
                return ctx.signal(
                    "short",
                    confidence=conf,
                    stop_loss_bps=self.METADATA["declared_sl_bps"],
                    take_profit_bps=self.METADATA["declared_tp_bps"],
                    metadata={
                        "reason": "vol_expansion_bearish_breakdown",
                        "atr": round(current_atr, 2),
                        "baseline_atr": round(baseline_atr, 2),
                        "atr_ratio": round(atr_ratio, 2),
                        "net_move": round(net_move, 2),
                        "threshold": round(move_threshold, 2),
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index