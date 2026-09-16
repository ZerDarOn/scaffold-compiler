"""Database schema revision. Review both directions before applying."""

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401
<% context.write(imports or "") %>

revision: str = <% context.write(repr(up_revision)) %>
down_revision: str | Sequence[str] | None = <% context.write(repr(down_revision)) %>
branch_labels: str | Sequence[str] | None = <% context.write(repr(branch_labels)) %>
depends_on: str | Sequence[str] | None = <% context.write(repr(depends_on)) %>


def upgrade() -> None:
    <% context.write(upgrades or "pass") %>


def downgrade() -> None:
    <% context.write(downgrades or "pass") %>
