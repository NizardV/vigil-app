"""PostgreSQL 16 + pgvector local pour le developpement, sans Docker ni installation systeme.

Lancer (Python 3.12 isole : `pgserver` n'a pas de version 3.13, et ce n'est pas une dependance du projet) :

    uv run --no-project --python 3.12 --with pgserver --with tzdata python -I infra/dev/postgres.py

Le serveur reste actif tant que ce processus tourne (Ctrl-C pour l'arreter). Les donnees sont
conservees dans `.pgdata/`. Les parametres de connexion sont ecrits dans `.pgdev.env` :

    set -a; . ./.pgdev.env; set +a        # puis lancer manage.py normalement

Usage dev uniquement : aucune authentification, ecoute sur 127.0.0.1.
"""

import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pgserver
import shutil
import tzdata

ROOT = Path(__file__).resolve().parents[2]
PGDATA = ROOT / ".pgdata"
ENV_FILE = ROOT / ".pgdev.env"


def ensure_timezone_data() -> None:
    """Le PostgreSQL embarque (Windows) n'a pas sa base de fuseaux ; tzdata fournit les memes fichiers TZif."""
    pginstall = Path(pgserver.__file__).parent / "pginstall"
    target = pginstall / "share" / "postgresql" / "timezone"
    if not target.exists():
        shutil.copytree(Path(tzdata.__file__).parent / "zoneinfo", target)


def main() -> int:
    ensure_timezone_data()
    server = pgserver.get_server(PGDATA, cleanup_mode="stop")
    uri = urlparse(server.get_uri())
    ENV_FILE.write_text(
        "\n".join(
            [
                "POSTGRES_DB=postgres",
                f"POSTGRES_USER={uri.username or 'postgres'}",
                "POSTGRES_PASSWORD=",
                f"POSTGRES_HOST={uri.hostname}",
                f"POSTGRES_PORT={uri.port}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"PostgreSQL pret sur {uri.hostname}:{uri.port} (parametres dans {ENV_FILE.name})", flush=True)

    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while not stop:
        time.sleep(0.5)
    server.cleanup()
    ENV_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
