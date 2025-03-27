ARG PYTHON_VERSION="3.12"

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PYTHONUNBUFFERED=1

WORKDIR /app
ARG TARGETARCH
ARG SIZE="full"
RUN python -m ensurepip --upgrade \
    && apt-get update && apt-get dist-upgrade -y \
    && apt-get install -y git curl libleveldb-dev \
    && if [ "$SIZE" = "full" -o "$TARGETARCH" = "arm64" ]; then apt-get install -y ffmpeg; fi \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

FROM base AS venv
ARG TARGETARCH

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN python -m ensurepip --upgrade && \
    pip3 install poetry poetry-plugin-export && \
    python -m venv /venv && \
    /venv/bin/pip install --upgrade pip && \
    poetry export -f requirements.txt  -o requirements.txt --ansi --without-hashes && \
    /venv/bin/pip install --no-cache-dir pyffmpeg==2.4.2.18.1 \
    --index-url https://pypi.org/simple && \
    /venv/bin/pip --no-cache-dir install -r requirements.txt && \
    rm requirements.txt

COPY . .

RUN  pip install dunamai \
    && poetry version $(poetry run dunamai from git --format "{base}" --pattern "(?P<base>\d+\.\d+\.\w+)") \
    && poetry build \
    && /venv/bin/pip install dist/*.whl

FROM base AS final

ARG TARGETARCH

COPY --from=venv /venv /venv

ENV PATH="/venv/bin:${PATH}" \
    VIRTUAL_ENV="/venv"

RUN addgroup --gid 1000 fansly && \
    adduser --uid 1000 --ingroup fansly \
    --home /home/fansly --shell /bin/sh \
    --disabled-password --gecos "" fansly && \
    USER=fansly && \
    GROUP=fansly && \
    LATEST_VERSION=$(curl -s https://api.github.com/repos/boxboat/fixuid/releases/latest | grep "tag_name"| cut -d'v' -f2 | cut -d'"' -f1) && \
    curl -SsL "https://github.com/boxboat/fixuid/releases/latest/download/fixuid-$LATEST_VERSION-linux-$TARGETARCH.tar.gz" | tar -C /usr/local/bin -xzf - && \
    echo "fansly ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers && \
    chown root:root /usr/local/bin/fixuid && \
    chmod 4755 /usr/local/bin/fixuid && \
    mkdir -p /etc/fixuid && \
    printf "user: $USER\ngroup: $GROUP\npaths:\n  - /home/fansly/\n" > /etc/fixuid/config.yml
USER fansly:fansly

ENTRYPOINT [ "fixuid", "-q" ]
