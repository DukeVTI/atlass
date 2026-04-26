FROM python:3.11-slim

WORKDIR /app

# Install ffmpeg (required by pydub for OGG → WAV conversion)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-bake the faster-whisper model so there's zero cold-start delay at runtime
# The model is cached in /app/.cache/huggingface inside the image layer
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"

COPY . .

CMD ["python", "main.py"]
