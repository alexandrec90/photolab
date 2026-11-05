"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}  # pragma: allowlist secret
## ruff would wrap a merge's tuple, leaving its ids on lines the pragma does not reach.
% if isinstance(down_revision, tuple):
down_revision: str | Sequence[str] | None = ${repr(down_revision)}  # pragma: allowlist secret  # fmt: skip
% else:
down_revision: str | Sequence[str] | None = ${repr(down_revision)}  # pragma: allowlist secret
% endif
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
