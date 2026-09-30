"""
stowaway/crypto.py

Cryptographic authentication and encryption module for Project Stowaway.
Uses PBKDF2HMAC with SHA-256 for key derivation and AES-128-CBC via Fernet
for authenticated symmetric encryption.
"""

import base64
import os
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def derive_key(password: str, salt: bytes) -> bytes:
    """Derive a URL-safe Base64-encoded 32-byte key from a password and salt.

    Args:
        password: The passphrase used for key derivation.
        salt: A 16-byte cryptographically secure random salt.

    Returns:
        bytes: A 32-byte URL-safe Base64-encoded key required by Fernet.

    Raises:
        TypeError: If inputs do not match required types.
        ValueError: If password is empty or salt is not exactly 16 bytes.
    """
    if not isinstance(password, str):
        raise TypeError("Password must be a string.")
    if not isinstance(salt, bytes):
        raise TypeError("Salt must be bytes.")
    if not password:
        raise ValueError("Password cannot be empty.")
    if len(salt) != 16:
        raise ValueError("Salt must be exactly 16 bytes long.")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    derived_raw = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(derived_raw)


def lock(message: str, password: str) -> tuple[bytes, bytes]:
    """Encrypt a plaintext string into an authenticated Fernet token.

    Args:
        message: The UTF-8 string payload to encrypt.
        password: The passphrase used to secure the payload.

    Returns:
        tuple[bytes, bytes]: A tuple of (salt, fernet_token).

    Raises:
        TypeError: If message or password are not strings.
        ValueError: If message or password are empty strings.
    """
    if not isinstance(message, str):
        raise TypeError("Message must be a string.")
    if not isinstance(password, str):
        raise TypeError("Password must be a string.")
    if not message:
        raise ValueError("Message cannot be empty.")
    if not password:
        raise ValueError("Password cannot be empty.")

    salt = os.urandom(16)
    key = derive_key(password, salt)
    fernet = Fernet(key)
    token = fernet.encrypt(message.encode("utf-8"))

    return salt, token


def unlock(token: bytes, password: str, salt: bytes) -> str:
    """Decrypt and verify an encrypted Fernet token using password and salt.

    Args:
        token: The encrypted Fernet token bytes.
        password: The passphrase used during encryption.
        salt: The original 16-byte salt generated during encryption.

    Returns:
        str: The decrypted UTF-8 plaintext string.

    Raises:
        TypeError: If parameters are of invalid types.
        ValueError: If token or salt are empty, or if decryption fails due to
                    wrong password or tampered token.
    """
    if not isinstance(token, bytes):
        raise TypeError("Token must be bytes.")
    if not isinstance(password, str):
        raise TypeError("Password must be a string.")
    if not isinstance(salt, bytes):
        raise TypeError("Salt must be bytes.")
    if not token:
        raise ValueError("Token cannot be empty.")

    try:
        key = derive_key(password, salt)
        fernet = Fernet(key)
        decrypted_bytes = fernet.decrypt(token)
        return decrypted_bytes.decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Tampered or wrong password") from exc
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError)) and "Tampered" not in str(exc):
            raise
        raise ValueError("Tampered or wrong password") from exc


if __name__ == "__main__":
    print("--- Running stowaway/crypto.py Sanity Verification ---")

    payload = "Stowaway Transmission Alpha-9: Coordinates Verified"
    passphrase = "correct-horse-battery-staple"

    # 1. Verification of standard Lock & Unlock flow
    salt, token = lock(payload, passphrase)
    decrypted = unlock(token, passphrase, salt)
    assert decrypted == payload, "Sanity Check Failed: Decrypted text does not match!"
    print("[PASS] Successful Encryption and Decryption")

    # 2. Verification of Wrong Password Rejection
    try:
        unlock(token, "wrong-passphrase", salt)
        print("[FAIL] Wrong password did not raise ValueError")
    except ValueError as e:
        assert str(e) == "Tampered or wrong password"
        print("[PASS] Wrong Password Detection")

    # 3. Verification of Payload Tampering Detection
    tampered_token = bytearray(token)
    tampered_token[-1] ^= 0xFF  # Corrupt the HMAC signature at the end of the token
    try:
        unlock(bytes(tampered_token), passphrase, salt)
        print("[FAIL] Tampered payload did not raise ValueError")
    except ValueError as e:
        assert str(e) == "Tampered or wrong password"
        print("[PASS] Tampered Payload Detection")

    # 4. Edge-Case: Empty String Handling
    try:
        lock("", passphrase)
        print("[FAIL] Empty message did not raise ValueError")
    except ValueError:
        print("[PASS] Empty Message Guard")

    print("\nAll sanity checks passed successfully!")# Password to key, lock and unlock
