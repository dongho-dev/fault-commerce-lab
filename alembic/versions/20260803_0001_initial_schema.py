"""Create the L1 commerce schema.

Revision ID: 20260803_0001
Revises:
Create Date: 2026-08-03 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260803_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_products_name_not_blank"),
        sa.CheckConstraint("unit_price >= 1", name="ck_products_unit_price_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
    )
    op.create_table(
        "inventories",
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("initial_stock", sa.Integer(), nullable=False),
        sa.Column("current_stock", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("initial_stock >= 0", name="ck_inventories_initial_non_negative"),
        sa.CheckConstraint("current_stock >= 0", name="ck_inventories_current_non_negative"),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="fk_inventories_product", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("product_id", name="pk_inventories"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.BigInteger(), nullable=False),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("shipping_fee", sa.BigInteger(), nullable=False),
        sa.Column("total_amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("quantity >= 1", name="ck_orders_quantity_positive"),
        sa.CheckConstraint("unit_price >= 1", name="ck_orders_unit_price_positive"),
        sa.CheckConstraint("btrim(postal_code) <> ''", name="ck_orders_postal_code_not_blank"),
        sa.CheckConstraint("shipping_fee >= 0", name="ck_orders_shipping_fee_non_negative"),
        sa.CheckConstraint(
            "total_amount = unit_price * quantity + shipping_fee",
            name="ck_orders_total_amount_equation",
        ),
        sa.CheckConstraint("status = 'CONFIRMED'", name="ck_orders_status_confirmed"),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name="fk_orders_product", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
    )
    op.create_index(
        "ix_orders_product_id_created_at",
        "orders",
        ["product_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_orders_product_id_created_at", table_name="orders")
    op.drop_table("orders")
    op.drop_table("inventories")
    op.drop_table("products")
