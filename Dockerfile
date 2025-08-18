FROM python:3.11-slim

ARG REQUIREMENTS_FILE

ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app
EXPOSE 8000

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-traditional \
    apt-transport-https \
    curl \
    gnupg \
    git \
    libpq-dev \
    gcc \
    binutils \
 && rm -rf /var/lib/apt/lists/*

# 🔧 Export GDAL paths
ENV PROJ_LIB=/usr/share/proj

# Install wait-for script
RUN curl -o /wait-for https://raw.githubusercontent.com/eficode/wait-for/master/wait-for \
 && chmod +x /wait-for

# Install Python dependencies
COPY ./requirements/ ./requirements
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir --timeout 100 -r ./requirements/${REQUIREMENTS_FILE}

# Copy app code
COPY . ./
