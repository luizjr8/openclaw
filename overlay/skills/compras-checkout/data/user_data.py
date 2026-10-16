"""
Load user data from USER.md for checkout forms.
"""

import os
import re
from pathlib import Path

WORKSPACE = Path(os.environ.get("LZBOT_WORKSPACE_ROOT", str(Path.cwd())))


def load_user_data() -> dict:
    """Extract checkout-relevant user data from USER.md."""
    user_md = WORKSPACE / "USER.md"
    if not user_md.exists():
        raise FileNotFoundError("USER.md not found")

    text = user_md.read_text()

    return {
        "fullname": _extract(text, r"\*\*Nome completo:\*\*\s*(.+)"),
        "email": _extract(text, r"\*\*Email:\*\*\s*(.+)"),
        "cpf": _extract(text, r"\*\*CPF:\*\*\s*([\d\.\-]+)"),
    }


def _extract(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None
