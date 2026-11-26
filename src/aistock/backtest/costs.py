"""Indian trading costs, as fractions of trade value: equity delivery (CNC)
and MCX commodity futures.

Rates are approximate NSE/MCX figures; DP charges and futures brokerage are flat fees.
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


@dataclass
class FuturesCostModel:
    """MCX non-agri futures costs: flat brokerage per order, CTT on the sell side.
    The same interface as CostModel; for a short, the entry is the sell."""
    brokerage_per_order: float = 20.0
    ctt_sell: float = 0.0001       # commodity transaction tax, sell side only
    exchange: float = 0.000021     # MCX transaction charge
    sebi: float = 0.000001
    stamp_buy: float = 0.00002     # buy side only
    gst: float = 0.18              # on brokerage + exchange + SEBI
    slippage: float = 0.0005       # per side, applied to fill price

    def _fees(self, value: float) -> float:
        return (self.brokerage_per_order + value * (self.exchange + self.sebi)) * (1 + self.gst)

    def buy_cost(self, value: float) -> float:
        return self._fees(value) + value * self.stamp_buy

    def sell_cost(self, value: float) -> float:
        return self._fees(value) + value * self.ctt_sell
