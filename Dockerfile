# One image, two roles: the web app (default command) and the worker (python -m pmr_web.worker).
FROM python:3.12-slim

# The commit being built; /version reports it. The deploy workflow passes it in.
ARG GIT_SHA=dev

ENV PMR_VERSION=$GIT_SHA \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PMR_DATA_DIR=/tmp/pmr \
    PMR_EMBEDDED_WORKER=0

WORKDIR /app
COPY requirements.txt requirements-web.txt pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements-web.txt

# The committed model answers seed the shared cache in Blob Storage on first start.
COPY llm_cache ./llm_cache
# The sample drop folder, offered on the New report screen for demonstrations.
COPY inputs ./inputs

RUN useradd --create-home pmr
USER pmr
EXPOSE 8000
# One process with threads: the app is I/O bound, and one process keeps schema creation free of races.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", "--timeout", "120", "pmr_web.app:create_app()"]
