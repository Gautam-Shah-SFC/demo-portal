from cryptography.fernet import Fernet

from common.paths import FERNET_KEY_FILE


def _get_fernet() -> Fernet:
    if not FERNET_KEY_FILE.exists():
        FERNET_KEY_FILE.write_bytes(Fernet.generate_key())
    return Fernet(FERNET_KEY_FILE.read_bytes())


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
