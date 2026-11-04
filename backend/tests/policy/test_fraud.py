from app.policy.fraud import requires_evidence


def test_none_and_empty_reason_never_requires_evidence() -> None:
    assert requires_evidence(None) is False
    assert requires_evidence("") is False
    assert requires_evidence("   ") is False


def test_benign_reasons_do_not_require_evidence() -> None:
    for reason in (
        "no longer needed",
        "ab zaroorat nahi hai",
        "duplicate order",
        "size chhota hai",
        "galti se order place kar diya",
    ):
        assert requires_evidence(reason) is False


def test_wrong_item_and_empty_box_require_evidence() -> None:
    assert requires_evidence("box mein Samsung nikla, wrong product") is True
    assert requires_evidence("box khola toh andar kuch mila hi nahi") is True
    assert requires_evidence("Empty box aaya hai") is True


def test_damaged_and_missing_item_require_evidence() -> None:
    assert requires_evidence("product toot kar aaya hai, damaged") is True
    assert requires_evidence("charger missing hai, sirf mobile mila") is True


def test_fake_and_used_condition_require_evidence() -> None:
    assert requires_evidence("ye to fake product lagta hai") is True
    assert requires_evidence("product pehle se used lag raha hai") is True


def test_case_and_script_insensitive() -> None:
    assert requires_evidence("WRONG PRODUCT bheja hai") is True
    assert requires_evidence("Wrong Item") is True


def test_wardrobing_admission_requires_evidence() -> None:
    # Section 12.1: an explicit admission of prior use is a signal, even though a plain
    # "doesn't fit"/"not needed" return is not (that's ordinary bracketing, Section 12.2).
    assert requires_evidence("ek baar function me pehna tha, ab return karna hai") is True
    assert requires_evidence("used it once, size thik nahi lagi") is True
    assert requires_evidence("tag hata diya tha dekhne ke liye, fit nahi hua") is True


def test_plain_bracketing_returns_do_not_require_evidence() -> None:
    for reason in ("size chhota hai", "fit nahi hua", "color pasand nahi aya", "need nahi hai"):
        assert requires_evidence(reason) is False
