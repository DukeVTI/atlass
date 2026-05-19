FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    procps \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py local_tools.py ./

# Create the scoped root directory
RUN mkdir -p /root/Documents/Atlas

ENV SCOPED_ROOT=/root/Documents/Atlas
ENV ATLAS_VPS_URL=ws://api:8000
ENV LOG_LEVEL=INFO

CMD ["python", "worker.py"]
