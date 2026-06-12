"""Validação interna (sistema vs. manual) + bug reports — repo in-memory."""
from backend.app.chat.domain.models import (
    BugReport, QuoteValidation, ValidationKind,
)
from backend.app.chat.repository.memory import InMemoryRepository


def _val(offer_id, kind, **kw):
    return QuoteValidation(
        user_id="u", thread_id="t", offer_id=offer_id, kind=kind,
        system_offer={"airline": "LATAM", "equivalent_brl": 1000, "route": "GRU→LIS"},
        **kw,
    )


def test_create_validated():
    r = InMemoryRepository()
    v = r.create_validation(_val("o1", ValidationKind.VALIDATED))
    assert v.kind == ValidationKind.VALIDATED
    assert len(r.list_validations("u")) == 1


def test_create_corrected_with_value():
    r = InMemoryRepository()
    r.create_validation(_val("o2", ValidationKind.CORRECTED, found_value_brl=800,
                             found_airline="GOL", emission_method="milhas",
                             found_program="Smiles"))
    items = r.list_validations("u", kind=ValidationKind.CORRECTED)
    assert len(items) == 1 and items[0].found_value_brl == 800


def test_idempotent_same_offer_kind():
    r = InMemoryRepository()
    r.create_validation(_val("o1", ValidationKind.VALIDATED))
    r.create_validation(_val("o1", ValidationKind.VALIDATED))  # duplo clique
    assert len(r.list_validations("u")) == 1                   # não duplica


def test_stats_mix_accuracy_and_delta():
    r = InMemoryRepository()
    r.create_validation(_val("o1", ValidationKind.VALIDATED))
    r.create_validation(_val("o2", ValidationKind.VALIDATED))
    r.create_validation(_val("o3", ValidationKind.CORRECTED, found_value_brl=600,
                             emission_method="cash_cia", found_airline="AZUL"))
    st = r.validation_stats("u")
    assert st["total"] == 3
    assert st["validated_count"] == 2 and st["corrected_count"] == 1
    assert st["accuracy_pct"] == 66.7                  # 2/3
    assert st["avg_delta_brl"] == 400.0                # 1000 − 600
    assert st["by_method"] == {"cash_cia": 1}
    assert st["by_airline"] == {"AZUL": 1}


def test_by_thread_and_user_isolation():
    r = InMemoryRepository()
    r.create_validation(_val("o1", ValidationKind.VALIDATED))
    r.create_validation(QuoteValidation(user_id="outro", thread_id="t",
                                        offer_id="ox", kind=ValidationKind.VALIDATED,
                                        system_offer={}))
    assert len(r.list_validations_by_thread("t", "u")) == 1   # só do user u
    assert len(r.list_validations("outro")) == 1              # isolado


def test_bug_report_create_and_list():
    r = InMemoryRepository()
    r.create_bug_report(BugReport(user_id="u", thread_id="t", description="deu erro X"))
    items = r.list_bug_reports("u")
    assert len(items) == 1 and items[0].description == "deu erro X"
    assert items[0].status == "open"
    assert r.list_bug_reports("outro") == []
