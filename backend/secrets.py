from __future__ import annotations

import os
import subprocess
from pathlib import Path
from subprocess import run as run_process

from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(RuntimeError):
    """Raised when local provider credentials cannot be decrypted."""


class SecretCipher:
    """Encrypt provider credentials with a database-adjacent, local master key."""

    def __init__(self, key_path: Path):
        self.key_path = key_path
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self._key = self._load_or_create_key()
        self._fernet = Fernet(self._key)

    @classmethod
    def for_database(cls, database_path: Path) -> "SecretCipher":
        return cls(database_path.with_suffix(database_path.suffix + ".key"))

    def _load_or_create_key(self) -> bytes:
        try:
            descriptor = os.open(
                self.key_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            key = self.key_path.read_bytes().strip()
            self._restrict_key_permissions()
            # Fernet validates the exact key format when it is constructed.
            return key
        key = Fernet.generate_key()
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(key + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            self.key_path.unlink(missing_ok=True)
            raise
        self._restrict_key_permissions()
        return key

    def _restrict_key_permissions(self) -> None:
        if os.name != "nt":
            os.chmod(self.key_path, 0o600)
            return

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        account_result = run_process(
            ["whoami"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            creationflags=flags,
        )
        account = account_result.stdout.strip()
        if account_result.returncode != 0 or not account:
            raise RuntimeError("Unable to identify the Windows account for key protection")

        acl_result = run_process(
            [
                "icacls",
                str(self.key_path),
                "/inheritance:r",
                "/grant:r",
                f"{account}:(R,W)",
            ],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            creationflags=flags,
        )
        if acl_result.returncode != 0:
            detail = (acl_result.stdout or acl_result.stderr).strip()
            raise RuntimeError(f"Unable to protect the Windows key file: {detail}")

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise SecretDecryptionError(
                "Provider credential cannot be decrypted with the local master key"
            ) from exc
