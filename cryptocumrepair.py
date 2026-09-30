import base64
from getpass import getpass

from crypto import lock, unlock
from repair import protect, repair

_B64 = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

def encrypt_text(message: str, password: str) -> str:
    salt, token = lock(message, password)
    protected = protect(salt + token)
    return base64.urlsafe_b64encode(protected).decode("ascii").rstrip("=")

def decrypt_text(packed: str, password: str) -> str:
    # no .strip(): a damaged character can look like whitespace and change the length
    cleaned = "".join(c if c in _B64 else "A" for c in packed)
    cleaned += "=" * (-len(cleaned) % 4)          # restore padding from the length
    protected = base64.urlsafe_b64decode(cleaned)
    data = repair(protected)
    salt, token = data[:16], data[16:]
    return unlock(token, password, salt)

def cryptocumrepair(choice,text):
    password = getpass("Password: ")

    if choice == "e":
        
        #print("\nEncrypted text:\n")
        secret = encrypt_text(text, password)
        return secret
    elif choice == "d":
        packed = text
        try:
            #print("\nOriginal text:\n")
            non_secret = decrypt_text(packed, password)
            return non_secret
        except Exception as e:
            print(f"\nFailed: {e}")