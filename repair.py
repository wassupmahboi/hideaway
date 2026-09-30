# Reed-Solomon encode and decode
"""
stowaway/repair.py

Reed-Solomon error-correction coding (ECC) module for Project Stowaway.
Appends parity bytes to payload data to recover from bit/byte corruption 
caused by space radiation and solar proton storms.
"""

import reedsolo


def protect(data: bytes, nsym: int = 100) -> bytes:
    """Appends Reed-Solomon parity bytes to raw data.

    Args:
        data: Raw payload bytes to protect.
        nsym: Number of error correction parity bytes (default: 32).
              Allows correcting up to nsym // 2 byte corruptions.

    Returns:
        bytes: Encoded payload containing original data + parity bytes.

    Raises:
        TypeError: If inputs are of incorrect type.
        ValueError: If data is empty or nsym is non-positive.
    """
    if not isinstance(data, bytes):
        raise TypeError("Data must be bytes.")
    if not isinstance(nsym, int):
        raise TypeError("nsym must be an integer.")
    if not data:
        raise ValueError("Data cannot be empty.")
    if nsym <= 0:
        raise ValueError("nsym must be greater than zero.")

    rs = reedsolo.RSCodec(nsym)
    return bytes(rs.encode(data))


def repair(encoded_data: bytes, nsym: int = 100) -> bytes:
    """Detects and repairs corrupted byte data using Reed-Solomon ECC.

    Args:
        encoded_data: Encoded byte stream containing data and parity bytes.
        nsym: Number of ECC parity bytes used during encoding (default: 32).

    Returns:
        bytes: Original uncorrupted data payload.

    Raises:
        TypeError: If inputs are of incorrect type.
        ValueError: If encoded_data is empty, or if corruption exceeds ECC capacity.
    """
    if not isinstance(encoded_data, bytes):
        raise TypeError("Encoded data must be bytes.")
    if not isinstance(nsym, int):
        raise TypeError("nsym must be an integer.")
    if not encoded_data:
        raise ValueError("Encoded data cannot be empty.")
    if nsym <= 0:
        raise ValueError("nsym must be greater than zero.")

    rs = reedsolo.RSCodec(nsym)
    try:
        decoded_data = rs.decode(encoded_data)[0]
        return bytes(decoded_data)
    except reedsolo.ReedSolomonError as exc:
        raise ValueError("Corruption exceeds error correction capacity.") from exc


if __name__ == "__main__":
    print("--- Running stowaway/repair.py Sanity Verification ---")

    payload = b"Stowaway Transmission: Interstellar Payload"
    parity_bytes = 32

    # 1. Verification of Encode & Decode
    protected = protect(payload, nsym=parity_bytes)
    repaired = repair(protected, nsym=parity_bytes)
    assert repaired == payload, "Sanity Check Failed: Repaired data does not match original!"
    print("[PASS] Successful ECC Encoding and Decoding")

    # 2. Verification of Corruption Recovery (up to nsym // 2 bytes)
    corrupted = bytearray(protected)
    # Corrupt 10 bytes (well within the 16-byte tolerance for nsym=32)
    for i in range(10):
        corrupted[i] ^= 0xFF

    repaired_corrupted = repair(bytes(corrupted), nsym=parity_bytes)
    assert repaired_corrupted == payload, "Sanity Check Failed: Could not repair corrupted payload!"
    print(f"[PASS] Successfully Repaired 10 Corrupted Bytes (Capacity: {parity_bytes // 2})")

    # 3. Verification of Unrecoverable Damage Detection
    heavily_corrupted = bytearray(protected)
    # Corrupt 20 bytes (exceeding the 16-byte tolerance for nsym=32)
    for i in range(20):
        heavily_corrupted[i] ^= 0xFF

    try:
        repair(bytes(heavily_corrupted), nsym=parity_bytes)
        print("[FAIL] Unrecoverable damage did not raise ValueError")
    except ValueError as e:
        assert str(e) == "Corruption exceeds error correction capacity."
        print("[PASS] Unrecoverable Damage Detection")

    print("\nAll sanity checks passed successfully!")

