from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from app.config import get_settings
from app.db.crypto import (
    DecryptionError,
    EncryptedText,
    EncryptionKeyError,
    KeyRing,
    key_ring,
    new_key,
    use_key_ring,
)

KEY_1 = new_key("k1")
KEY_2 = new_key("k2")
CONTEXT = "journal_messages.text"


@pytest.fixture(autouse=True)
def fresh_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """No key ring in use, and settings read from a clean environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    use_key_ring(None)
    get_settings.cache_clear()
    yield
    use_key_ring(None)
    get_settings.cache_clear()


def test_a_round_trip() -> None:
    ring = KeyRing.parse(KEY_1)
    assert ring.decrypt(ring.encrypt("Shipped the export page.", CONTEXT), CONTEXT) == (
        "Shipped the export page."
    )


def test_ciphertext_hides_the_text_and_never_repeats() -> None:
    ring = KeyRing.parse(KEY_1)
    first, second = ring.encrypt("Shipped.", CONTEXT), ring.encrypt("Shipped.", CONTEXT)
    assert first != second
    assert b"Shipped" not in first
    assert ring.key_id(first) == "k1"


def test_the_first_key_encrypts_and_older_keys_still_decrypt() -> None:
    old = KeyRing.parse(KEY_1).encrypt("Old note.", CONTEXT)
    ring = KeyRing.parse(f"{KEY_2}, {KEY_1}")
    assert ring.decrypt(old, CONTEXT) == "Old note."
    assert ring.key_id(ring.encrypt("New note.", CONTEXT)) == "k2"


def test_a_missing_key_is_named() -> None:
    blob = KeyRing.parse(KEY_1).encrypt("Note.", CONTEXT)
    with pytest.raises(DecryptionError, match="no key 'k1' in DATA_ENCRYPTION_KEY"):
        KeyRing.parse(KEY_2).decrypt(blob, CONTEXT)


def test_the_wrong_key_fails_clearly() -> None:
    blob = KeyRing.parse(KEY_1).encrypt("Note.", CONTEXT)
    impostor = KeyRing.parse("k1:" + new_key("x").split(":")[1])
    with pytest.raises(DecryptionError, match="doesn't match key 'k1'"):
        impostor.decrypt(blob, CONTEXT)


def test_values_are_bound_to_their_column() -> None:
    ring = KeyRing.parse(KEY_1)
    with pytest.raises(DecryptionError):
        ring.decrypt(ring.encrypt("Note.", CONTEXT), "facts.statement")


def test_tampering_is_detected() -> None:
    ring = KeyRing.parse(KEY_1)
    blob = bytearray(ring.encrypt("Note.", CONTEXT))
    blob[-1] ^= 1
    with pytest.raises(DecryptionError):
        ring.decrypt(bytes(blob), CONTEXT)


@pytest.mark.parametrize("value", [b"plain text", b"\x01", b""])
def test_other_bytes_are_not_encrypted_data(value: bytes) -> None:
    with pytest.raises(DecryptionError):
        KeyRing.parse(KEY_1).decrypt(value, CONTEXT)


@pytest.mark.parametrize(
    "value",
    ["", "k1", "k1:!!!", "k1:c2hvcnQ", "K1:" + KEY_1.split(":")[1], f"{KEY_1},{KEY_1}"],
)
def test_malformed_keys_are_rejected(value: str) -> None:
    with pytest.raises(EncryptionKeyError):
        KeyRing.parse(value)


def test_new_keys_parse() -> None:
    assert KeyRing.parse(new_key("k3")).current_id == "k3"
    with pytest.raises(EncryptionKeyError):
        new_key("Not an ID")


def test_the_keys_come_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(EncryptionKeyError, match="set DATA_ENCRYPTION_KEY"):
        key_ring()
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", f"{KEY_2},{KEY_1}")
    get_settings.cache_clear()
    assert key_ring().current_id == "k2"


def test_the_column_type_leaves_empty_values_alone() -> None:
    column = EncryptedText(CONTEXT)
    dialect = postgresql.dialect()
    assert column.process_bind_param(None, dialect) is None
    assert column.process_result_value(None, dialect) is None
