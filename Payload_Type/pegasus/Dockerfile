FROM python:3.11-slim

WORKDIR /Mythic/

# Build tools for native PyInstaller (Linux) + Wine for Windows cross-compilation
RUN apt-get update && \
    dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        binutils \
        wine64 \
        wget \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Native Python PyInstaller for Linux builds
RUN pip install --no-cache-dir mythic_container azure-storage-blob pyinstaller

# Windows Python embeddable for cross-compilation via Wine
# Embeddable zip is lightweight (~20MB) and runs cleanly under Wine
ENV WINEDEBUG=-all
RUN mkdir -p /opt/win-python && \
    wget -q "https://www.python.org/ftp/python/3.14.0/python-3.14.0b3-embed-amd64.zip" \
        -O /tmp/winpy.zip && \
    unzip -q /tmp/winpy.zip -d /opt/win-python && \
    rm /tmp/winpy.zip && \
    sed -i 's/#import site/import site/' /opt/win-python/python314._pth && \
    wget -q "https://bootstrap.pypa.io/get-pip.py" -O /tmp/get-pip.py && \
    wine64 /opt/win-python/python.exe /tmp/get-pip.py \
        --no-warn-script-location 2>/dev/null && \
    rm /tmp/get-pip.py && \
    wine64 /opt/win-python/python.exe -m pip install \
        --no-cache-dir pyinstaller 2>/dev/null ; \
    true

COPY [".", "."]

CMD ["python3", "main.py"]
