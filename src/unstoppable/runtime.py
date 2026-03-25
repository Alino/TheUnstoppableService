from __future__ import annotations

from unstoppable.config import PAYMENT_EXECUTOR_CMD, PAYMENT_EXECUTOR_MODE
from unstoppable.payment_exec import (
    CommandPaymentExecutor,
    HybridPaymentExecutor,
    MockPaymentExecutor,
    PaymentExecutionError,
    PaymentExecutor,
)


def build_payment_executor() -> PaymentExecutor:
    mode = PAYMENT_EXECUTOR_MODE
    if mode == "command":
        try:
            return HybridPaymentExecutor(CommandPaymentExecutor(PAYMENT_EXECUTOR_CMD))
        except PaymentExecutionError:
            return HybridPaymentExecutor(MockPaymentExecutor())
    return HybridPaymentExecutor(MockPaymentExecutor())
