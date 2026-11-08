# Image de production de Vigil (web + worker partagent la même image).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src
# openssh-client : l'audit du VPS se connecte en SSH (clé restreinte, voir infra/audit).
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 vigil
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY src ./src
# collectstatic n'a besoin d'aucun secret ni d'aucune vraie base (valeur factice exigée par les réglages).
RUN DJANGO_DEBUG=1 POSTGRES_DB=build python src/manage.py collectstatic --noinput && chown -R vigil /app/src
USER vigil
WORKDIR /app/src
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=4).status==200 else 1)"
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]
