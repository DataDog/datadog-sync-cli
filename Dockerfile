FROM python:3.11-slim-buster

# Install Dependencies
RUN apt-get update -y \
    && apt-get install -y --no-install-recommends git \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

# Install CLI
COPY . /datadog-sync-cli
RUN pip install /datadog-sync-cli

VOLUME ["/datadog-sync"]

WORKDIR /datadog-sync

ENTRYPOINT ["datadog-sync"]
