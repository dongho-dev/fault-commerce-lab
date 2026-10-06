import os
import subprocess

from lab_suite.catalog import normalize, provider


def main() -> None:
    if os.environ.get("LAB_MIGRATE", "1") == "1":
        subprocess.run(["alembic", "upgrade", "head"], check=True)
    case = normalize(os.environ["LAB_CASE"])
    provider(case).install_runtime(case)
    import uvicorn

    from app.main import app

    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)


if __name__ == "__main__":
    main()
