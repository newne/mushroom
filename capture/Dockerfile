# 使用官方 Python slim 作为基础镜像，减少 apt 依赖/下载量，提高构建成功率
# 某些网络环境 Docker Hub 访问不稳定时，可通过 build-arg 覆盖为镜像源（例如 docker.m.daocloud.io/...）
# 说明：厂商 SDK 的 libXCloudSDK.so 往往依赖较旧的运行库（如 libssl1.1 / glibc 版本）。
# 为了最大化兼容性，默认使用 Debian bullseye（glibc 2.31 / OpenSSL 1.1）。
ARG BASE_IMAGE=python:3.10-slim-bullseye
FROM ${BASE_IMAGE}

ARG APT_MIRROR_URL=https://deb.debian.org

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN set -eux; \
    # Improve APT robustness for flaky networks / proxies
    printf '%s\n' \
      'Acquire::Retries "5";' \
      'Acquire::http::Timeout "60";' \
      'Acquire::https::Timeout "60";' \
      'Acquire::http::Pipeline-Depth "0";' \
      'Acquire::BrokenProxy "true";' \
      > /etc/apt/apt.conf.d/99mushroom_cli; \
    # Switch to HTTPS (and allow overriding mirror)
    sed -i "s|http://deb.debian.org|${APT_MIRROR_URL}|g" /etc/apt/sources.list; \
    sed -i "s|https://deb.debian.org|${APT_MIRROR_URL}|g" /etc/apt/sources.list; \
    # Retry install a few times (APT can error with unexpected EOF under unstable networks)
    for i in 1 2 3; do \
      if apt-get update && apt-get install -y --no-install-recommends --fix-missing \
        ca-certificates \
        xvfb \
        xauth \
        libx11-6 \
        libxext6; then \
        break; \
      fi; \
      echo "apt-get failed (attempt ${i}/3), retrying..." >&2; \
      sleep 5; \
    done; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only project sources (SDK .so/config/saved data should be mounted at runtime)
COPY pyproject.toml README.md /app/
COPY src /app/src
COPY docs /app/docs
COPY examples /app/examples
COPY scripts /app/scripts
COPY systemd /app/systemd
COPY minio_config.example.json /app/minio_config.example.json
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

RUN python3 -m pip install --no-cache-dir -U pip --retries 10 --timeout 120 \
 && python3 -m pip install --no-cache-dir . --retries 10 --timeout 120

RUN mkdir -p /app/lib/x86_64/Release /app/saved_datas/picture /app/log /app/Device

EXPOSE 7003
ENTRYPOINT ["/entrypoint.sh"]
