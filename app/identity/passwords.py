import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

MIN_PASSWORD_CHARACTERS = 12
MAX_PASSWORD_CHARACTERS = 1_024
MAX_PASSWORD_BYTES = 4_096


class PasswordHashError(ValueError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True, slots=True)
class ScryptPasswordHasher:
    log_n: int = 15
    r: int = 8
    p: int = 1
    salt_bytes: int = 16
    derived_key_bytes: int = 32
    max_memory_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if not 14 <= self.log_n <= 20:
            raise ValueError("scrypt log_n must be between 14 and 20")
        if not 1 <= self.r <= 32:
            raise ValueError("scrypt r must be between 1 and 32")
        if not 1 <= self.p <= 16:
            raise ValueError("scrypt p must be between 1 and 16")
        if self.salt_bytes < 16:
            raise ValueError("Password salts must contain at least 16 bytes")
        if self.derived_key_bytes < 32:
            raise ValueError("Password hashes must contain at least 32 bytes")

    def hash(self, password: str) -> str:
        password_bytes = self._validate_password(password)
        salt = secrets.token_bytes(self.salt_bytes)
        derived_key = self._derive(password_bytes, salt)
        parameters = f"ln={self.log_n},r={self.r},p={self.p}"
        return f"scrypt${parameters}${_encode(salt)}${_encode(derived_key)}"

    def verify(self, password: str, encoded_hash: str) -> bool:
        try:
            password_bytes = self._validate_password(password)
            log_n, r, p, salt, expected = self._parse(encoded_hash)
            actual = hashlib.scrypt(
                password_bytes,
                salt=salt,
                n=1 << log_n,
                r=r,
                p=p,
                dklen=len(expected),
                maxmem=self.max_memory_bytes,
            )
        except (PasswordHashError, ValueError, TypeError):
            return False
        return hmac.compare_digest(actual, expected)

    def needs_rehash(self, encoded_hash: str) -> bool:
        try:
            log_n, r, p, salt, expected = self._parse(encoded_hash)
        except (PasswordHashError, ValueError, TypeError):
            return True
        return (
            log_n != self.log_n
            or r != self.r
            or p != self.p
            or len(salt) != self.salt_bytes
            or len(expected) != self.derived_key_bytes
        )

    def _derive(self, password: bytes, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password,
            salt=salt,
            n=1 << self.log_n,
            r=self.r,
            p=self.p,
            dklen=self.derived_key_bytes,
            maxmem=self.max_memory_bytes,
        )

    @staticmethod
    def _validate_password(password: str) -> bytes:
        if not MIN_PASSWORD_CHARACTERS <= len(password) <= MAX_PASSWORD_CHARACTERS:
            raise PasswordHashError(
                f"Password must contain {MIN_PASSWORD_CHARACTERS} to "
                f"{MAX_PASSWORD_CHARACTERS} characters"
            )
        encoded = password.encode("utf-8")
        if len(encoded) > MAX_PASSWORD_BYTES:
            raise PasswordHashError("Password exceeds the encoded size limit")
        return encoded

    @staticmethod
    def _parse(encoded_hash: str) -> tuple[int, int, int, bytes, bytes]:
        parts = encoded_hash.split("$")
        if len(parts) != 4 or parts[0] != "scrypt":
            raise PasswordHashError("Unsupported password hash format")
        parameter_values = {}
        for item in parts[1].split(","):
            key, separator, value = item.partition("=")
            if not separator or key not in {"ln", "r", "p"} or key in parameter_values:
                raise PasswordHashError("Invalid scrypt parameters")
            parameter_values[key] = int(value)
        if set(parameter_values) != {"ln", "r", "p"}:
            raise PasswordHashError("Incomplete scrypt parameters")
        log_n = parameter_values["ln"]
        r = parameter_values["r"]
        p = parameter_values["p"]
        if not 14 <= log_n <= 20 or not 1 <= r <= 32 or not 1 <= p <= 16:
            raise PasswordHashError("Unsafe scrypt parameters")
        salt = _decode(parts[2])
        expected = _decode(parts[3])
        if len(salt) < 16 or not 32 <= len(expected) <= 64:
            raise PasswordHashError("Invalid scrypt hash length")
        return log_n, r, p, salt, expected
