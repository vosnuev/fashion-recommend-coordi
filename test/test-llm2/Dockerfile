# test-llm2: Gemini 3.1 Flash Image 편집 + Confluence 태깅 (API 호출만 — GPU 불필요)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run_all.py .
COPY common/ common/

# input/, output/ 은 호스트 볼륨 마운트 전제
VOLUME ["/app/input", "/app/output"]

ENTRYPOINT ["python", "run_all.py"]
