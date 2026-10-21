FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8080

# 状態の読み書きが競合しないよう、ワーカーは1つにする
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 120 main:app
