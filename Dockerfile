# Una sola imagen para la API, el ejecutor y las herramientas; el comando decide qué corre.
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 10001 olterra
COPY --from=build /install /usr/local
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
USER olterra
EXPOSE 8000
CMD ["uvicorn", "--factory", "olterra.api.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
