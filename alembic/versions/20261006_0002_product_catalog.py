"""Add storefront catalog attributes to products.

Revision ID: 20261006_0002
Revises: 20260803_0001
Create Date: 2026-10-06 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261006_0002"
down_revision: str | None = "20260803_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("category", sa.String(length=40), server_default="etc", nullable=False),
    )
    op.add_column(
        "products",
        sa.Column("brand", sa.String(length=80), server_default="", nullable=False),
    )
    op.add_column(
        "products",
        sa.Column("description", sa.Text(), server_default="", nullable=False),
    )
    op.add_column("products", sa.Column("list_price", sa.BigInteger(), nullable=True))
    op.add_column("products", sa.Column("image_url", sa.String(length=300), nullable=True))
    op.create_check_constraint(
        "ck_products_list_price_not_below_unit_price",
        "products",
        "list_price IS NULL OR list_price >= unit_price",
    )
    op.create_index("ix_products_category", "products", ["category"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_products_category", table_name="products")
    op.drop_constraint(
        "ck_products_list_price_not_below_unit_price", "products", type_="check"
    )
    op.drop_column("products", "image_url")
    op.drop_column("products", "list_price")
    op.drop_column("products", "description")
    op.drop_column("products", "brand")
    op.drop_column("products", "category")
