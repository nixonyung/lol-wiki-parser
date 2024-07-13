# NOTE: assuming build context is at project root directory

FROM python:3.12 AS base

ARG PLAYWRIGHT_VERSION=1.44.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        unzip=* \
        && \
    rm -rf /var/lib/apt/lists/*

# (ref.) [How to avoid reinstalling packages when building Docker image for Python projects?](https://stackoverflow.com/questions/25305788/how-to-avoid-reinstalling-packages-when-building-docker-image-for-python-project)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install playwright==$PLAYWRIGHT_VERSION && \
    python -m playwright install --with-deps chromium


FROM base

WORKDIR /app

COPY . .
# (ref.) [How to avoid reinstalling packages when building Docker image for Python projects?](https://stackoverflow.com/questions/25305788/how-to-avoid-reinstalling-packages-when-building-docker-image-for-python-project)
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

CMD ["python", "-m", "main"]
