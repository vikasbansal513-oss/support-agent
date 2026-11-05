FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Patches OS-level packages (apt, etc.) in the base image at build time, so a known CVE in
# the Debian base (e.g. the Trivy "image-scan" CI job catching a HIGH-severity apt CVE) is
# fixed here instead of waiting for python:3.13-slim itself to be rebuilt upstream.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

COPY constraints.txt pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install -c constraints.txt . && pip uninstall -y pip

RUN useradd --system --uid 10001 appuser
USER appuser

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
