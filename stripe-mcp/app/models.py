"""Enums vendored from github.com/sandraschi/stripe-mcp
(src/stripe_mcp/models.py), trimmed to the read-only surface this build
exposes. The write-operation enums (CustomerOp.CREATE, PaymentOp.ISSUE_REFUND,
SubscriptionOp.CANCEL, the whole CheckoutOp) are intentionally not carried
over -- no tool here can perform those actions.
"""

from enum import Enum


class AnalyticsMetric(str, Enum):
    MRR = "mrr"
    CHURN = "churn"
    DISPUTES = "disputes"
    VAT_SUMMARY = "vat_summary"
    ALL = "all"


class AustrianVatType(str, Enum):
    STANDARD_20 = "standard_20"
    REDUCED_10 = "reduced_10"
    REDUCED_13 = "reduced_13"
