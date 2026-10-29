FROM python:3.11-slim AS builder

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
    python3 \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 https://github.com/LibreDWG/libredwg.git . && \
    sh autogen.sh && \
    ./configure --disable-bindings --prefix=/usr/local && \
    make -j$(nproc) && \
    make install

# Runtime stage leve
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# Copia os binários compilados do libredwg
COPY --from=builder /usr/local/bin/dwg* /usr/local/bin/
COPY --from=builder /usr/local/lib/libredwg* /usr/local/lib/
COPY --from=builder /usr/local/include/dwg* /usr/local/include/
RUN ldconfig

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
