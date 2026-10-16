FROM python:3.12-slim

WORKDIR /app
RUN useradd --create-home --uid 10001 hylde
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
USER hylde
ENV HYLDE_DATA_DIR=/data \
    HYLDE_LOCAL_BASE_URL=http://host.docker.internal:11434/v1
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "hylde", "--mcp"]
