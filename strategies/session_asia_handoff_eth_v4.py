from domains.strategy_contract import Strategy, BarContext, Signal
from typing import Optional, Dict, Any
import math

class SessionAsiaHandoffEth(Strategy):
    METADATA = {
        "name": "Session Asia Handoff ETH",
        "domain": "eth_usdc",
        "declared_sl_bps": 200.0,
        "declared_tp_bps": 400.0,
        "declared_hold_seconds": 3600 * 4,  # Approx 4 hours, covering Asia session + handoff
        "warmup_bars": 50,
        "required_features": [],
    }

    def initialize(self, ctx: BarContext) -> None:
        self.session_length = 8  # Asia session UTC 0-8
        self.entry_threshold = 0.002  # 0.2% move to signify impulse
        self.cooldown_bars = 4  # Cooldown after a trade
        self.last_trade_bar = -self.cooldown_bars # Initialize to allow immediate trade

        # Indicators for confirmation and regime filtering
        self.ema_short_period = 20
        self.ema_long_period = 50
        self.rsi_period = 14
        self.atr_period = 14
        self.vol_sma_period = 20 # For average volume calculation

    def _ema(self, values, period):
        if len(values) < period:
            return None
        k = 2.0 / (period + 1)
        ema = sum(values[:period]) / period
        for v in values[period:]:
            ema = v * k + ema * (1 - k)
        return ema

    def _sma(self, values, period):
        if len(values) < period:
            return None
        return sum(values[-period:]) / period

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        
        avg_gain = self._sma(gains[-period:], period)
        avg_loss = self._sma(losses[-period:], period)

        if avg_gain is None or avg_loss is None:
            return None

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def on_bar(self, ctx: BarContext) -> Optional[Signal]:
        closes = ctx.closes(self.warmup_bars + 1)
        if len(closes) < self.warmup_bars:
            return None

        # Cooldown logic
        if ctx.bar_index < self.last_trade_bar + self.cooldown_bars:
            return None

        current_hour = ctx.bar.timestamp.hour

        # Calculate indicators
        ema_short = self._ema(closes, self.ema_short_period)
        ema_long = self._ema(closes, self.ema_long_period)
        rsi = self._rsi(closes, self.rsi_period)

        # Regime filters: only trade in 'normal' or 'volatile' regimes
        # and when EMA is not flat (i.e. some trend is present)
        if ctx.market.get("regime", "NORMAL") not in ["NORMAL", "HIGH_VOL"]:
            return None
        
        if ema_short is None or ema_long is None or rsi is None:
            return None

        # Only look for entry at the end of Asia session (UTC 7)
        # This gives us the full 8 bars of the session to calculate impulse
        if current_hour == 7 and not ctx.has_position():
            session_closes = ctx.closes(self.session_length)
            session_opens = ctx.opens(self.session_length)
            session_volumes = ctx.volumes(self.session_length)

            if len(session_closes) < self.session_length:
                return None
            
            # Calculate session impulse
            session_open_price = session_opens[0]
            session_close_price = session_closes[-1]
            session_change = (session_close_price - session_open_price) / session_open_price

            # Calculate average volume for confirmation
            avg_volume = self._sma(session_volumes, self.vol_sma_period)
            current_volume = ctx.bar.volume
            
            # Check for significant directional impulse and volume confirmation
            # Long condition
            if session_change > self.entry_threshold and current_volume > avg_volume * 1.5:
                # Confirm with EMA crossover (short above long) and RSI not overbought
                if ema_short > ema_long and rsi < 70:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "long",
                        confidence=min(0.7 + abs(session_change) * 50, 0.95), # Confidence scales with impulse
                        metadata={
                            "reason": "asia_session_bullish_handoff_ema_confirm",
                            "session_change_pct": session_change * 100,
                            "session_open": session_open_price,
                            "session_close": session_close_price,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "rsi": rsi,
                            "current_volume": current_volume,
                            "avg_volume": avg_volume,
                        }
                    )
            # Short condition
            elif session_change < -self.entry_threshold and current_volume > avg_volume * 1.5:
                # Confirm with EMA crossover (short below long) and RSI not oversold
                if ema_short < ema_long and rsi > 30:
                    self.last_trade_bar = ctx.bar_index
                    return ctx.signal(
                        "short",
                        confidence=min(0.7 + abs(session_change) * 50, 0.95), # Confidence scales with impulse
                        metadata={
                            "reason": "asia_session_bearish_handoff_ema_confirm",
                            "session_change_pct": session_change * 100,
                            "session_open": session_open_price,
                            "session_close": session_close_price,
                            "ema_short": ema_short,
                            "ema_long": ema_long,
                            "rsi": rsi,
                            "current_volume": current_volume,
                            "avg_volume": avg_volume,
                        }
                    )
        
        # Exit logic: Close position before UTC 8 (end of Asian session handoff)
        # Or if EMA crossover signals a reversal
        if ctx.has_position():
            # Time-based exit before 8 UTC
            if current_hour == 8:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.5,
                    metadata={
                        "reason": "time_based_exit_asia_handoff_boundary",
                        "current_hour": current_hour,
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "rsi": rsi,
                    }
                )
            
            # EMA reversal exit
            if ctx.position_direction() == "long" and ema_short < ema_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_crossover_reversal_exit_long",
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "rsi": rsi,
                    }
                )
            elif ctx.position_direction() == "short" and ema_short > ema_long:
                self.last_trade_bar = ctx.bar_index
                return ctx.signal(
                    "flat",
                    confidence=0.6,
                    metadata={
                        "reason": "ema_crossover_reversal_exit_short",
                        "ema_short": ema_short,
                        "ema_long": ema_long,
                        "rsi": rsi,
                    }
                )

        return None

    def on_position_close(self, ctx: BarContext, position: Dict[str, Any]) -> None:
        # No adaptive logic for now, simply log
        pass