FROM python:slim-buster@sha256:6fd99c1c6bac8abf9952cb797dc409ac92e8fbedd4d080211381a69c213b509b

# Install CLI
COPY . /datadog-sync-cli
RUN pip install /datadog-sync-cli

VOLUME ["/datadog-sync"]

WORKDIR /datadog-sync

ENTRYPOINT ["datadog-sync"]
