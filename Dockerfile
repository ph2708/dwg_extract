# Builder stage para compilar/obter o libredwg
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    autoconf \
    automake \
    libtool \
    pkg-config \
    texinfo \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 https://github.com/LibreDWG/libredwg.git . && \
    sh autogen.sh && \
    ./configure --disable-bindings --prefix=/usr/local && \
    make -j$(nproc) && \
    make install

# Runtime stage
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copiar bibliotecas e binários compilados do libredwg
COPY --from=builder /usr/local/bin/dwg* /usr/local/bin/
COPY --from=builder /usr/local/lib/libredwg* /usr/local/lib/
COPY --from=builder /usr/local/include/dwg* /usr/local/include/
RUN ldconfig

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código da aplicação
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
