FROM python:3.11-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Dépendances système pour psycopg2
RUN apk add --no-cache gcc musl-dev libpq-dev

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV RUN_ENV=local

CMD ["python", "main.py"]