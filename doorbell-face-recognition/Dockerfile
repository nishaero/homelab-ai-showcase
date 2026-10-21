FROM python:3.12-slim
RUN pip install --no-cache-dir websockets httpx psycopg2-binary pillow
WORKDIR /app
COPY doorbell_face.py .
CMD ["python","-u","doorbell_face.py"]
