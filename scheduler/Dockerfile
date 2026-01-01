# Image Python légère
FROM python:3.11-slim

# Empêche Python de créer des .pyc
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Dossier de travail
WORKDIR /app

# Dépendances minimales (aucun navigateur, aucun Selenium)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code du scheduler
COPY . .

# Variable d'environnement par défaut
ENV RUN_ENV=local

# Lancement du scheduler
CMD ["python", "main.py"]
