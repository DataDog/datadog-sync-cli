FROM python:slim-buster@sha256:6fd99c1c6bac8abf9952cb797dc409ac92e8fbedd4d080211381a69c213b509b

RUN apt update && apt install wget unzip -y

# Install CLI
COPY . /datadog-sync-cli
RUN pip install /datadog-sync-cli

# Intall terraform and terraformer
ENV TF_VERSION=0.12.31
ENV TERRAFORMER_VERSION=0.8.13
RUN wget https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip \
    && wget https://github.com/GoogleCloudPlatform/terraformer/releases/download/${TERRAFORMER_VERSION}/terraformer-datadog-linux-amd64 \
    && unzip terraform_${TF_VERSION}_linux_amd64.zip -d /usr/local/bin/ \
    && mv terraformer-datadog-linux-amd64 /usr/local/bin/terraformer \
    && chmod +x /usr/local/bin/terraformer /usr/local/bin/terraform

VOLUME ["/datadog-sync"]

WORKDIR /datadog-sync

ENTRYPOINT ["datadog-sync"]