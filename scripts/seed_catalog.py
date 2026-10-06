import argparse
import json

from app.catalog import seed_catalog
from app.database import SessionFactory


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Insert the demo storefront catalog. Existing product names are skipped."
    )


def main() -> int:
    build_parser().parse_args()
    with SessionFactory() as session:
        inserted = seed_catalog(session)
    print(json.dumps({"inserted": len(inserted), "names": inserted}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
