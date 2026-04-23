FROM python:3.11-slim

WORKDIR /app

# Copy memory models/schemas/embeddings from orchestrator
COPY services/orchestrator/memory ./memory

# Copy memory service requirements and code
COPY services/memory/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/memory/main.py .

CMD ["python", "main.py"]
