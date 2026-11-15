FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dépendances d'abord : cette couche reste en cache tant que le verrou ne change pas.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# Le répertoire est créé dans l'image avec le bon propriétaire : un volume
# nommé monté dessus hérite de ces droits à sa création. Sans cela il
# appartiendrait à root et l'atelier refuserait de démarrer (volume non inscriptible).
RUN useradd --system --uid 10001 atelier \
    && mkdir -p /data/images \
    && chown atelier /data/images
USER atelier

EXPOSE 8000
CMD ["/app/.venv/bin/atelier"]
