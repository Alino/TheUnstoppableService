from __future__ import annotations

from unstoppable.payment_exec import HybridPaymentExecutor
import unstoppable.runtime as runtime


def test_runtime_falls_back_to_mock_when_command_missing(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "PAYMENT_EXECUTOR_MODE", "command")
    monkeypatch.setattr(runtime, "PAYMENT_EXECUTOR_CMD", "")
    executor = runtime.build_payment_executor()
    assert isinstance(executor, HybridPaymentExecutor)
    result = executor.execute({"id": "i1"}, {"id": "p1"})
    assert result.txid.startswith("sim-")
