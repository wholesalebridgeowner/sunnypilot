# Drive Advisor Railway service. Build context: ram1500-development/advisor/
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir boto3==1.35.36
COPY *.py /app/
ENV PYTHONUNBUFFERED=1
CMD ["python3", "service_app.py"]
