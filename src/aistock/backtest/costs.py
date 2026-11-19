"""Indian equity delivery (CNC) trading costs, as fractions of trade value.

Rates are approximate NSE figures; DP charges are a flat fee per sell.
"""
from dataclasses import dataclass


@dataclass
class CostModel:
    brokerage: float = 0.0        # most discount brokers charge 0 on delivery
    stt: float = 0.001            # 0.1% on buy and sell
    exchange: float = 0.0000297   # NSE transaction charge
    sebi: float = 0.000001
    stamp_buy: float = 0.00015    # buy side only
    gst: float = 0.18             # on brokerage + exchange + SEBI
    dp_per_sell: float = 15.93    # flat, per scrip sold
    slippage: float = 0.0005      # per side, applied to fill price

    def buy_cost(self, value: float) -> float:
        fees = value * (self.brokerage + self.exchange + self.sebi)
        return fees * (1 + self.gst) + value * (self.stt + self.stamp_buy)

    def sell_cost(self, value: float) -> float:
        fees = value * (self.brokerage + self.exchange + self.sebi)
        return fees * (1 + self.gst) + value * self.stt + self.dp_per_sell
