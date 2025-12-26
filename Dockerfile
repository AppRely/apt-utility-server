FROM python:3.11-slim

ARG REQUIREMENTS_FILE

ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app
EXPOSE 8000

# Install system/build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-traditional \
    apt-transport-https \
    curl \
    gnupg \
    git \
    gcc \
    g++ \
    make \
    binutils \
    build-essential \
    python3-dev \
    libpq-dev \
    libgtk-3-dev \
    libgl1-mesa-dev \
    libglu1-mesa-dev \
    libsdl2-dev \
    libgstreamer-plugins-base1.0-dev \
    libnotify-dev \
    freeglut3-dev \
    libsm-dev \
    libxext-dev \
    libxrender-dev \
    libjpeg-dev \
    libtiff-dev \
    libhdf5-dev \
    libopenblas-dev \
    liblapack-dev \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libv4l-dev \
    libxvidcore-dev \
    libx264-dev \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*


# Optional: For GDAL or PROJ (only if your app uses these)
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

COPY ./docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
