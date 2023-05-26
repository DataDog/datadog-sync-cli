FROM python:3.11-slim-buster

# Install CLI
COPY . /datadog-sync-cli
RUN pip install /datadog-sync-cli

VOLUME ["/datadog-sync"]

WORKDIR /datadog-sync

ENTRYPOINT ["datadog-sync"]
