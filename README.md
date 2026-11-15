# Atelier d'annonces candidates

De l'URL d'achat à l'annonce prête. Application autonome : FastAPI + Jinja2 + HTMX
(servi localement, sans étape de build), PostgreSQL dédiée via asyncpg.

## Lancer

```bash
cp .env.example .env   # puis renseigner les valeurs
docker compose up --build
```

L'application écoute sur <http://localhost:8000>, santé sur `/api/health`
(200 si la base répond, 503 sinon).

## Configuration (variables d'environnement)

| Variable | Rôle |
|---|---|
| `ATELIER_DATABASE_URL` | chaîne de connexion PostgreSQL (obligatoire) |
| `ATELIER_IMAGES_ROOT` | racine du volume d'images, doit être inscriptible (obligatoire) |
| `ATELIER_CAPTURE_TOKEN` | jeton du raccourci de capture (obligatoire) |
| `ATELIER_HTTP_HOST` / `ATELIER_HTTP_PORT` | adresse d'écoute (défaut `127.0.0.1:8000`) |
| `ATELIER_DB_CONNECT_TIMEOUT_S` | délai de connexion à la base au démarrage (défaut 10 s) |

Au démarrage, l'atelier vérifie le volume, se connecte à la base, puis applique les
migrations manquantes (`src/atelier/migrations/NNNN_nom.sql`, jamais modifiées une fois
appliquées). Tout échec s'affiche en une ligne explicite et le processus sort en code 1.

## Développement

```bash
uv sync
export ATELIER_TEST_ADMIN_DSN=postgresql://postgres@localhost/postgres  # serveur de test
uv run pytest --cov=src
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

Les tests PostgreSQL créent une base jetable par exécution sur le serveur désigné par
`ATELIER_TEST_ADMIN_DSN` ; sans cette variable, ils sont ignorés.
