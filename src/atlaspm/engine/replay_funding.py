"""Synthetic financing ledger used only by offline session replay.

Uniform, assumed long-equity margin fractions; no brokerage data or authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from atlaspm.engine.contracts import finite


@dataclass
class MarginFunding:
    target_gross: float
    initial_fraction: float
    maintenance_fraction: float
    annual_apr: float
    principal: float = 0.
    accrued: float = 0.
    interest_total: float = 0.
    borrowed_total: float = 0.
    repaid_total: float = 0.
    interest_paid_total: float = 0.

    @classmethod
    def from_policy(cls, policy):
        if not isinstance(policy,dict) or set(policy)!={"target_gross","initial_fraction","maintenance_fraction","annual_apr"}:
            raise ValueError("INVALID_SYNTHETIC_MARGIN_POLICY")
        target=finite(policy["target_gross"],"target_gross",minimum=1)
        maintenance=finite(policy["maintenance_fraction"],"maintenance_fraction",minimum=.000001)
        initial=finite(policy["initial_fraction"],"initial_fraction",minimum=maintenance)
        apr=finite(policy["annual_apr"],"annual_apr",minimum=0)
        if initial>1 or apr>1 or target>min(4.,1/initial):
            raise ValueError("SYNTHETIC_MARGIN_TARGET_EXCEEDS_ASSUMED_INITIAL_LIMIT")
        return cls(target,initial,maintenance,apr)

    @property
    def liability(self):
        return self.principal+self.accrued

    @property
    def initial_gross_cap(self):
        return min(4.,1/self.initial_fraction)

    def accrue(self,days):
        # Date-to-date convention: prior close principal, simple ACT/365.
        # No interest on accrued interest; no claim about broker settlement.
        if type(days) is not int or days<0:
            raise ValueError("INVALID_SYNTHETIC_ACCRUAL_DAYS")
        charge=self.principal*self.annual_apr*days/365
        self.accrued+=charge
        self.interest_total+=charge
        return charge

    def repay(self,cash):
        cash=finite(cash,"repayment_cash",minimum=0)
        paid=min(cash,self.accrued)
        self.accrued-=paid
        self.interest_paid_total+=paid
        cash-=paid
        paid=min(cash,self.principal)
        self.principal-=paid
        self.repaid_total+=paid
        return cash-paid

    def pay_purchase(self,cash,amount):
        cash=finite(cash,"purchase_cash",minimum=0)
        amount=finite(amount,"purchase_amount",minimum=0)
        borrowed=max(0.,amount-cash)
        self.principal+=borrowed
        self.borrowed_total+=borrowed
        return max(0.,cash-amount)

    def buy_budget(self,*,cash,pending,gross,equity,adverse_cost_ratio,gross_limit=None):
        """Maximum purchase cash outlay under both gross and debt bounds.

        Exclude unsettled receivables from the equity used to fund new risk.
        For reference notional x and cost <= k*x, the post-fill condition
        gross+x <= cap*(equity-pending-k*x) bounds x. The debt bound
        separately ensures loan+accrual <= (cap-1)*funding equity after cost.
        The result is conservative: it caps total outlay by reference
        notional, which itself never exceeds total outlay on a buy.
        """
        k=finite(adverse_cost_ratio,"adverse_cost_ratio",minimum=0)
        if k>=1:
            raise ValueError("INVALID_MARGIN_COST_BOUND")
        cap=self.initial_gross_cap
        if gross_limit is not None:
            cap=min(cap,finite(gross_limit,"gross_limit",minimum=1.))
        e=max(0.,equity-pending)
        gross_bound=(cap*e-gross)/(1+cap*k)
        debt_bound=(cash+(cap-1)*e-self.liability)/(1+(cap-1)*k)
        return max(0.,min(gross_bound,debt_bound))

    def snapshot(self,gross,equity):
        maint=gross*self.maintenance_fraction
        return {"principal":self.principal,"accrued_interest":self.accrued,
                "maintenance_assumption":maint,"maintenance_excess":equity-maint,
                "maintenance_breach":equity<=0 or equity<maint-1e-8}

    def totals(self):
        return {"margin_debt":self.principal,"accrued_financing_interest":self.accrued,
                "financing_interest_total":self.interest_total,"borrowed_total":self.borrowed_total,
                "principal_repaid_total":self.repaid_total,"interest_paid_total":self.interest_paid_total}
