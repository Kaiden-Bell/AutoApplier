"""
    Encryption at rest for profile data (CV, application profile, notification email).

    AES-256-GCM with a random 96-bit nonce per value. The associated data binds each ciphertext to
    its account and field, so a value copied into another row or column fails to decrypt.

    Format: b"\\x01" (format version) + nonce (12 bytes) + ciphertext-with-tag.
"""

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_NONCE_BYTES = 12


class DataKeyError(Exception):
    """The data key is missing or malformed, or a value failed to decrypt."""


def generate_key():
    """A new random key, base64url-encoded, for AUTOAPPLIER_DATA_KEY."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def load_key(encoded):
    if not encoded:
        raise DataKeyError("AUTOAPPLIER_DATA_KEY is not configured on the server.")
    try:
        key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError) as e:
        raise DataKeyError("AUTOAPPLIER_DATA_KEY is not valid base64.") from e
    if len(key) != 32:
        raise DataKeyError("AUTOAPPLIER_DATA_KEY must decode to 32 bytes.")
    return key


def _aad(account_id, field):
    return f"autoapplier:{account_id}:{field}".encode("utf-8")


def encrypt_json(key, account_id, field, value):
    """Encrypts a JSON-serializable value. None stays None."""
    if value is None:
        return None
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return _VERSION + nonce + AESGCM(key).encrypt(nonce, plaintext, _aad(account_id, field))


def decrypt_json(key, account_id, field, blob):
    """Decrypts a value from encrypt_json. None stays None."""
    if blob is None:
        return None
    blob = bytes(blob)
    if len(blob) < 1 + _NONCE_BYTES + 16 or blob[:1] != _VERSION:
        raise DataKeyError(f"Stored {field} is not in a recognized format.")
    nonce, ciphertext = blob[1:1 + _NONCE_BYTES], blob[1 + _NONCE_BYTES:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(account_id, field))
    except InvalidTag as e:
        raise DataKeyError(f"Stored {field} could not be decrypted (wrong key or altered data).") from e
    return json.loads(plaintext)
