"""Encrypted text columns (FR-JRN-4, NFR-SEC-2): journal and fact text are ciphertext at rest.

Values are encrypted with AES-256-GCM, which also detects tampering. The key comes from
`DATA_ENCRYPTION_KEY`, outside the database. The setting holds one or more keys, as
`id:key` pairs separated by commas. The first key encrypts; the others can still decrypt, which is
what makes rotation possible:

1. `wj keys new` prints a new key, such as `k2:...`.
2. Put it first in `DATA_ENCRYPTION_KEY`, keeping the old key after it: `k2:...,k1:...`.
3. `wj keys rotate` re-encrypts everything with the new key.
4. Remove the old key.

Each stored value is a small header (format version and key ID), a random nonce, then the
ciphertext. The column's name is authenticated too, so a value copied to another column won't
decrypt.
"""

import base64
import re
import secrets
from collections.abc import Sequence
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import LargeBinary, MetaData, select, type_coerce, update
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

FORMAT = 1
KEY_BYTES = 32
NONCE_BYTES = 12
_KEY_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,15}")


class EncryptionKeyError(Exception):
    """`DATA_ENCRYPTION_KEY` is missing or malformed."""


class DecryptionError(Exception):
    """A stored value can't be decrypted: an unknown or wrong key, or damaged data."""


class KeyRing:
    """The keys from `DATA_ENCRYPTION_KEY`. The first one encrypts; any of them decrypts."""

    def __init__(self, keys: Sequence[tuple[str, bytes]]) -> None:
        if not keys:
            raise EncryptionKeyError("DATA_ENCRYPTION_KEY has no keys")
        ids = [key_id for key_id, _ in keys]
        if len(set(ids)) != len(ids):
            raise EncryptionKeyError("DATA_ENCRYPTION_KEY lists a key ID twice")
        for key_id, key in keys:
            if not _KEY_ID.fullmatch(key_id):
                raise EncryptionKeyError(f"not a key ID: {key_id!r} (use a-z, 0-9 and -)")
            if len(key) != KEY_BYTES:
                raise EncryptionKeyError(f"key {key_id!r} isn't {KEY_BYTES} bytes")
        self.current_id = ids[0]
        self._ciphers = {key_id: AESGCM(key) for key_id, key in keys}

    @classmethod
    def parse(cls, value: str) -> "KeyRing":
        """Keys written as `id:base64url-key`, separated by commas, the current key first."""
        keys: list[tuple[str, bytes]] = []
        for entry in value.split(","):
            key_id, separator, encoded = entry.strip().partition(":")
            if not separator:
                raise EncryptionKeyError("write each key as id:key, e.g. k1:... from `wj keys new`")
            try:
                key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            except ValueError:
                raise EncryptionKeyError(f"key {key_id!r} isn't base64") from None
            keys.append((key_id, key))
        return cls(keys)

    def encrypt(self, text: str, context: str) -> bytes:
        key_id = self.current_id
        header = bytes([FORMAT, len(key_id)]) + key_id.encode()
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = self._ciphers[key_id].encrypt(nonce, text.encode(), _aad(header, context))
        return header + nonce + ciphertext

    def decrypt(self, blob: bytes, context: str) -> str:
        key_id, header, rest = _split(blob)
        cipher = self._ciphers.get(key_id)
        if cipher is None:
            raise DecryptionError(
                f"no key {key_id!r} in DATA_ENCRYPTION_KEY; add it back to read this data"
            )
        nonce, ciphertext = rest[:NONCE_BYTES], rest[NONCE_BYTES:]
        try:
            return cipher.decrypt(nonce, ciphertext, _aad(header, context)).decode()
        except InvalidTag:
            raise DecryptionError(
                f"the data doesn't match key {key_id!r}: the key is wrong, or the data is damaged"
                " or belongs to another column"
            ) from None

    def key_id(self, blob: bytes) -> str:
        """Which key encrypted `blob`."""
        return _split(blob)[0]


def new_key(key_id: str) -> str:
    """A new random key, written as `DATA_ENCRYPTION_KEY` expects."""
    if not _KEY_ID.fullmatch(key_id):
        raise EncryptionKeyError(f"not a key ID: {key_id!r} (use a-z, 0-9 and -)")
    encoded = base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode().rstrip("=")
    return f"{key_id}:{encoded}"


_active: KeyRing | None = None


def key_ring() -> KeyRing:
    """The keys in use: set with `use_key_ring`, or read from the settings on first use."""
    global _active
    if _active is None:
        value = get_settings().data_encryption_key
        if value is None:
            raise EncryptionKeyError(
                "set DATA_ENCRYPTION_KEY to store or read journal text; `wj keys new` makes a key"
            )
        _active = KeyRing.parse(value.get_secret_value())
    return _active


def use_key_ring(ring: KeyRing | None) -> None:
    """Use `ring` from now on, or read the settings again when it's None."""
    global _active
    _active = ring


class EncryptedText(TypeDecorator[str]):
    """Text stored encrypted. `context` names the column, e.g. `journal_messages.text`."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, context: str) -> None:
        super().__init__()
        self.context = context

    def process_bind_param(self, value: str | None, dialect: Dialect) -> bytes | None:
        return None if value is None else key_ring().encrypt(value, self.context)

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else key_ring().decrypt(bytes(value), self.context)


async def rotate_keys(session: AsyncSession, metadata: MetaData) -> dict[str, int]:
    """Re-encrypt every value that isn't under the current key. Returns counts by column."""
    ring = key_ring()
    counts: dict[str, int] = {}
    for table in metadata.sorted_tables:
        primary_key = list(table.primary_key.columns)
        for column in table.columns:
            if not isinstance(column.type, EncryptedText):
                continue
            raw = type_coerce(column, LargeBinary)
            rows = await session.execute(select(*primary_key, raw).where(column.is_not(None)))
            changed = 0
            for *key_values, blob in rows:
                if ring.key_id(bytes(blob)) == ring.current_id:
                    continue
                text = ring.decrypt(bytes(blob), column.type.context)
                match = [part == value for part, value in zip(primary_key, key_values, strict=True)]
                await session.execute(update(table).where(*match).values({column.name: text}))
                changed += 1
            counts[f"{table.name}.{column.name}"] = changed
    return counts


def _aad(header: bytes, context: str) -> bytes:
    return header + b"\x00" + context.encode()


def _split(blob: bytes) -> tuple[str, bytes, bytes]:
    """The key ID, the header and the rest of an encrypted value."""
    if len(blob) < 2 or blob[0] != FORMAT:
        raise DecryptionError("the value isn't encrypted data")
    end = 2 + blob[1]
    key_id = blob[2:end].decode(errors="replace")
    if len(blob) < end + NONCE_BYTES + 16:
        raise DecryptionError("the encrypted value is truncated")
    return key_id, blob[:end], blob[end:]
