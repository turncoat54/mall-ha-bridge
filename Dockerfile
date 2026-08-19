# 国内网络拉不到镜像时可覆盖:
#   docker build --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
#                --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    MALL_HA_CONFIG=/app/config/config.yaml

COPY requirements.txt ./
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} -r requirements.txt

COPY src/mall_ha_bridge ./mall_ha_bridge
COPY scripts/simulate_order.py ./scripts/simulate_order.py

# 以非 root 用户运行
RUN useradd -r -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "mall_ha_bridge"]
