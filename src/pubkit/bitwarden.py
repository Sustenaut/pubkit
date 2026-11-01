"""Writing a secret to Bitwarden Secrets Manager with Bitwarden's own SDK.

The bws CLI takes a value only on its command line, where every local user can
read it for as long as the write takes. The SDK takes it in memory, so a command
that writes something secret comes here.

The SDK is not a dependency of pubkit, which installs into every repository's test
environment, and it has compiled wheels for only some platforms. `ensure` installs it
for this run alone, from bitwarden-sdk.lock, by hash, as what it is given is a write
token and a private key. uv checks the hashes only with --require-hashes on `uv pip
install`: `uvx --with-requirements` installs a wrong one without a word.
"""

from __future__ import annotations

import atexit
import shutil
import subprocess
import sys
import tempfile
from importlib import resources
from uuid import UUID

LOCK = "bitwarden-sdk.lock"


class WriteFailed(Exception):
    """A write Bitwarden refused, with hidden values taken out of its message."""


def scrub(text: str, hidden: tuple[str, ...]) -> str:
    """`text` with every hidden value, and each line of one, taken out."""
    for value in hidden:
        for part in [value, *value.splitlines()]:
            if part:
                text = text.replace(part, "[hidden]")
    return text


def ensure() -> str | None:
    """Return nothing once the SDK can be imported, or say why it can't."""
    try:
        import bitwarden_sdk  # noqa: F401, PLC0415
    except ImportError:
        pass
    else:
        return None
    if shutil.which("uv") is None:
        return "this command fetches Bitwarden's SDK with uv, which is not installed: curl -LsSf https://astral.sh/uv/install.sh | sh"
    target = tempfile.mkdtemp(prefix="pubkit-bitwarden-")
    atexit.register(shutil.rmtree, target, ignore_errors=True)
    with resources.as_file(resources.files("pubkit") / LOCK) as lock:
        done = subprocess.run(["uv", "pip", "install", "--quiet", "--require-hashes", "--python", sys.executable,
                               "--target", target, "-r", str(lock)], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return f"Bitwarden's SDK could not be installed: {done.stderr.strip()}"
    sys.path.insert(0, target)
    try:
        import bitwarden_sdk  # noqa: F401, PLC0415
    except ImportError as error:
        return f"Bitwarden's SDK was installed but can't be imported: {error}"
    return None


def write(token: str, organization: str, project: str, name: str, value: str, note: str,
          secret: str | None = None) -> None:
    """Create the secret `name`, or update `secret`, in the project. The token and the value
    stay in this process."""
    from bitwarden_sdk import BitwardenClient  # noqa: PLC0415
    hidden = (token, value)
    try:
        client = BitwardenClient()
        answer = client.auth().login_access_token(token)
        if answer.success:
            if secret is None:
                answer = client.secrets().create(UUID(organization), name, value, note, [UUID(project)])
            else:
                answer = client.secrets().update(organization, secret, name, value, note, [UUID(project)])
    except Exception as error:  # noqa: BLE001  # the SDK raises bare Exceptions, and they may quote what it was given
        raise WriteFailed(scrub(str(error), hidden) or type(error).__name__) from None
    if not answer.success:
        raise WriteFailed(scrub(str(answer.error_message), hidden))
