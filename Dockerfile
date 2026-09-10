FROM python:3.11-slim

ARG TA_LIB_VERSION=0.4.0
ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

# Build native dependencies and the TA-Lib C library inside the image. No host
# compiler, headers, or TA-Lib installation is part of the runtime contract.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        ca-certificates \
        gcc \
        g++ \
        libffi-dev \
        libpq-dev \
        libssl-dev \
        make \
        gzip \
        tar \
        pkg-config \
        wget \
    && wget --quiet --output-document=/tmp/ta-lib.tar.gz \
        "https://github.com/TA-Lib/ta-lib/releases/download/v${TA_LIB_VERSION}/ta-lib-${TA_LIB_VERSION}-src.tar.gz" \
    && mkdir --parents /tmp/ta-lib-src \
    && tar --extract --gzip --file=/tmp/ta-lib.tar.gz --strip-components=1 --directory=/tmp/ta-lib-src \
    && cd /tmp/ta-lib-src \
    && ./configure --prefix=/usr/local \
    && make \
    && make install \
    && ldconfig \
    && rm --recursive --force /tmp/ta-lib-src /tmp/ta-lib.tar.gz \
    && rm --recursive --force /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement /tmp/requirements.txt \
    && rm --force /tmp/requirements.txt

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin app \
    && mkdir --parents /app/data /app/logs \
    && chown --recursive app:app /app

COPY --chown=app:app . /app
RUN if [ -f /app/pyproject.toml ]; then \
        python -m pip install --no-cache-dir /app; \
    fi

USER app

CMD ["python", "-m", "trading_bot"]
