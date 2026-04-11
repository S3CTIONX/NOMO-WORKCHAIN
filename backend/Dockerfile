FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Expose port (Railway auto-detects)
EXPOSE 8000

# Start the server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
