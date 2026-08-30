# 옷장 이미지 프로세서 (AI Worker)
# gemini-edit 파이프라인은 GPU가 필수는 아니지만(생성·태깅이 API 호출),
# 임베딩(FashionSigLIP/bge-m3) 가속을 위해 CUDA 베이스를 사용한다.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py worker.py reindex_worker.py ./
COPY pipeline/ pipeline/
COPY services/ services/

CMD ["python", "worker.py"]
