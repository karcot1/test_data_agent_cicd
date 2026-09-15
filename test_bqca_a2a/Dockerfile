FROM python:3.11-slim

WORKDIR /app

COPY __init__.py .
COPY agent.py .
COPY config.json .
COPY main.py .
COPY requirements.txt .
RUN pip install -r requirements.txt

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]