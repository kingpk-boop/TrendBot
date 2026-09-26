# TrendBot for an always-on cloud server (see "Run it in the cloud" in README.md).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY web web
COPY run.py .
# Bots and trade history live here: mount a persistent disk at /data.
ENV TRENDBOT_DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "run.py", "--host", "0.0.0.0", "--no-browser"]
