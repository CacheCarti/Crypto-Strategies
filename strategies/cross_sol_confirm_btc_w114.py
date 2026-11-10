from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class CrossAssetLeaderFollower(Strategy):
    METADATA = {
        "name": "Cross-Asset Leader-Follower BTC",
        "domain": "btc_usdc",
        "declared_sl_bps": 250.0,
        "declared_tp_bps": 500.0,
        "declared_hold_seconds": 7200, # 2-hour average hold
        "warmup_bars": 50,
        "required_features": ["sol_return_pct", "btc_return_pct"],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.fast_ema_period = 10
        self.slow_ema_period = 30
        self.rsi_period = 14
        self.cooldown_bars = 5 # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.slow_ema_period + self.rsi_period)
        if len(closes) < self.METADATA["warmup_bars"]:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        # Calculate indicators
        fast_ema = self._ema(closes, self.fast_ema_period)
        slow_ema = self._ema(closes, self.slow_ema_period)
        rsi = self._rsi(closes, self.rsi_period)

        if fast_ema is None or slow_ema is None or rsi is None:
            return None

        # Get cross-asset returns
        sol_return_pct = ctx.features.get("sol_return_pct", 0.0)
        btc_return_pct = ctx.features.get("btc_return_pct", 0.0)

        # Leader-Follower Logic: SOL (leader) dictates direction, BTC (target) confirms
        # We look for a fresh, significant move in SOL that BTC has not fully caught up to,
        # but is starting to confirm.

        # Exit logic:
        # 1. Relationship normalizes (BTC return aligns with SOL return)
        # 2. RSI neutral (overbought/oversold condition resolved)
        if ctx.has_position():
            position_direction = ctx.position_direction()
            entry_price = ctx.position["entry_price"]
            current_price = ctx.bar.close

            # If SOL return reverses or BTC return matches SOL return
            if position_direction == "long" and (sol_return_pct < -0.5 or btc_return_pct >= sol_return_pct * 0.95 or rsi > 60):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "long_exit_sol_reversal_or_btc_catchup_or_rsi_neutral",
                    "sol_return_pct": sol_return_pct,
                    "btc_return_pct": btc_return_pct,
                    "current_rsi": rsi,
                    "entry_price": entry_price,
                    "current_price": current_price
                })
            elif position_direction == "short" and (sol_return_pct > 0.5 or btc_return_pct <= sol_return_pct * 0.95 or rsi < 40):
                return ctx.signal("flat", confidence=0.7, metadata={
                    "reason": "short_exit_sol_reversal_or_btc_catchup_or_rsi_neutral",
                    "sol_return_pct": sol_return_pct,
                    "btc_return_pct": btc_return_pct,
                    "current_rsi": rsi,
                    "entry_price": entry_price,
                    "current_price": current_price
                })
            return None # Hold position if no exit condition met

        # Entry logic
        signal_direction = None
        confidence = 0.0
        reason = ""

        # Long entry conditions:
        # 1. SOL showing strong positive return (leader)
        # 2. BTC showing positive return but less than SOL (follower confirming but with lag)
        # 3. BTC price is above fast EMA and fast EMA is above slow EMA (local uptrend)
        # 4. BTC RSI is not overbought (potential for further upside)
        if (sol_return_pct > 1.0 and # SOL strong positive move
            0.1 < btc_return_pct < sol_return_pct * 0.8 and # BTC confirming, but still lagging
            ctx.bar.close > fast_ema and fast_ema > slow_ema and # Local trend confirmation
            rsi < 65): # Not overbought yet
            signal_direction = "long"
            confidence = 0.75
            reason = "cross_asset_long_sol_leading_btc_confirming_ema_bullish_rsi_not_overbought"

        # Short entry conditions:
        # 1. SOL showing strong negative return (leader)
        # 2. BTC showing negative return but less than SOL (follower confirming but with lag)
        # 3. BTC price is below fast EMA and fast EMA is below slow EMA (local downtrend)
        # 4. BTC RSI is not oversold (potential for further downside)
        elif (sol_return_pct < -1.0 and # SOL strong negative move
              -0.1 > btc_return_pct > sol_return_pct * 0.8 and # BTC confirming, but still lagging
              ctx.bar.close < fast_ema and fast_ema < slow_ema and # Local trend confirmation
              rsi > 35): # Not oversold yet
            signal_direction = "short"
            confidence = 0.75
            reason = "cross_asset_short_sol_leading_btc_confirming_ema_bearish_rsi_not_oversold"

        if signal_direction:
            self.last_trade_bar = ctx.bar_index
            return ctx.signal(
                signal_direction,
                confidence=confidence,
                metadata={
                    "reason": reason,
                    "sol_return_pct": sol_return_pct,
                    "btc_return_pct": btc_return_pct,
                    "fast_ema": fast_ema,
                    "slow_ema": slow_ema,
                    "current_rsi": rsi,
                    "current_price": ctx.bar.close
                }
            )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # Optional: Log position close for analysis or adaptive sizing
        pass