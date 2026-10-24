FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    FORGE_DB_PATH=/data/forge.db
WORKDIR /app
COPY . /app
EXPOSE 8000
CMD ["python", "server.py"]
