from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class EthRsiDivergenceSwing(Strategy):
    METADATA = {
        "name": "EthRsiDivergenceSwing",
        "domain": "eth_usdc",
        "declared_sl_bps": 220.0,
        "declared_tp_bps": 380.0,
        "declared_hold_seconds": 14400,
        "warmup_bars": 45,
    }

    def initialize(self, ctx: BarContext) -> None:
        self.rsi_period = 14
        self.lookback = 35
        self.cooldown_bars = 6
        self.last_exit_bar = -999
        self.entry_bar = -999

    def _calc_rsi_series(self, closes):
        n = len(closes)
        if n < self.rsi_period + 1:
            return []
        
        gains, losses = [], []
        for i in range(1, n):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
            
        period = self.rsi_period
        rsi_series = []
        for i in range(period, len(gains) + 1):
            sub_gains = gains[i-period:i]
            sub_losses = losses[i-period:i]
            avg_gain = sum(sub_gains) / period
            avg_loss = sum(sub_losses) / period
            if avg_loss == 0.0:
                rsi_series.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi_series.append(100.0 - (100.0 / (1.0 + rs)))
        return rsi_series

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        self.last_exit_bar = ctx.bar_index

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.lookback + self.rsi_period + 5)
        if len(closes) < self.lookback + self.rsi_period:
            return None

        rsi_series = self._calc_rsi_series(closes)
        if len(rsi_series) < self.lookback:
            return None

        current_rsi = rsi_series[-1]
        current_close = closes[-1]
        
        # Check active position management / exits
        if ctx.has_position():
            pos_dir = ctx.position_direction()
            bars_held = ctx.bar_index - self.entry_bar
            
            # Normalization exits or time stop
            if pos_dir == "long" and (current_rsi >= 58.0 or bars_held >= 18):
                return ctx.signal(
                    "flat", 
                    confidence=0.6,
                    metadata={"reason": "rsi_normalized_long_exit", "rsi": round(current_rsi, 2), "bars_held": bars_held}
                )
            elif pos_dir == "short" and (current_rsi <= 42.0 or bars_held >= 18):
                return ctx.signal(
                    "flat", 
                    confidence=0.6,
                    metadata={"reason": "rsi_normalized_short_exit", "rsi": round(current_rsi, 2), "bars_held": bars_held}
                )
            return None

        # Cooldown guard
        if ctx.bar_index - self.last_exit_bar < self.cooldown_bars:
            return None

        # Regime gate to address prior failure: avoid entering counter-divergences in crisis / high-vol regime
        market_regime = ctx.market.get("regime", "NORMAL")
        crisis_score = ctx.market.get("crisis_score", 0.0)
        if market_regime in ("CRISIS", "MELTDOWN") or crisis_score > 0.4:
            return None

        # Lookback analysis for divergence pivots
        window_closes = closes[-self.lookback:]
        window_rsi = rsi_series[-self.lookback:]

        # Find swing low pivot (between 4 and 25 bars ago)
        # Bullish divergence: price reaches a lower low today compared to pivot low, but RSI forms a higher low
        bullish_div = False
        bearish_div = False
        pivot_rsi_val = 0.0
        pivot_close_val = 0.0

        # Scan for bullish divergence setup
        if current_rsi < 36.0:
            for i in range(5, 26):
                idx = len(window_closes) - 1 - i
                p_c = window_closes[idx]
                p_r = window_rsi[idx]
                
                # Verify local minimum for price
                if p_c < window_closes[idx-1] and p_c < window_closes[idx+1] and p_c < window_closes[idx-2] and p_c < window_closes[idx+2]:
                    if p_r < 35.0:
                        if current_close < p_c and current_rsi > (p_r + 2.0):
                            bullish_div = True
                            pivot_rsi_val = p_r
                            pivot_close_val = p_c
                            break

        # Scan for bearish divergence setup
        if not bullish_div and current_rsi > 64.0:
            for i in range(5, 26):
                idx = len(window_closes) - 1 - i
                p_c = window_closes[idx]
                p_r = window_rsi[idx]
                
                # Verify local maximum for price
                if p_c > window_closes[idx-1] and p_c > window_closes[idx+1] and p_c > window_closes[idx-2] and p_c > window_closes[idx+2]:
                    if p_r > 65.0:
                        if current_close > p_c and current_rsi < (p_r - 2.0):
                            bearish_div = True
                            pivot_rsi_val = p_r
                            pivot_close_val = p_c
                            break

        if bullish_div:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "long",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bullish_rsi_divergence",
                    "current_rsi": round(current_rsi, 2),
                    "pivot_rsi": round(pivot_rsi_val, 2),
                    "current_close": round(current_close, 2),
                    "pivot_close": round(pivot_close_val, 2),
                }
            )

        if bearish_div:
            self.entry_bar = ctx.bar_index
            return ctx.signal(
                "short",
                confidence=0.75,
                stop_loss_bps=self.METADATA["declared_sl_bps"],
                take_profit_bps=self.METADATA["declared_tp_bps"],
                horizon_seconds=self.METADATA["declared_hold_seconds"],
                metadata={
                    "reason": "bearish_rsi_divergence",
                    "current_rsi": round(current_rsi, 2),
                    "pivot_rsi": round(pivot_rsi_val, 2),
                    "current_close": round(current_close, 2),
                    "pivot_close": round(pivot_close_val, 2),
                }
            )

        return None