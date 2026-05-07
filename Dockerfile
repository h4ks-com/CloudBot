FROM python:3.10.14-bullseye

WORKDIR /app

RUN \
  apt-get update && \
  apt-get install -y --no-install-recommends \
    enchant-2 \
    libenchant-2-2

COPY --from=ghcr.io/astral-sh/uv:0.7.17 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project

COPY . /app
RUN uv sync

CMD ["uv", "run", "python", "-m", "cloudbot"]
