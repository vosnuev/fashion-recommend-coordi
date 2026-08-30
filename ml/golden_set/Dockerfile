# 골든셋 임베딩 워커.
#
# 빌드 컨텍스트는 리포 루트다 (indexer/ image-processor/ ml/ 세 곳을 모두 쓴다).
#   docker compose -f docker-compose.gpu.yml build golden-set
#
# image-processor를 함께 넣는 이유: 아이템 분리·태깅 파이프라인을 재구현하지
# 않고 그대로 import한다 (ml/golden_set/items.py 참고). image-processor 모듈이
# 최상위 `config`를 import하는 구조라 PYTHONPATH에 디렉터리째 올린다.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /app

# ── 의존성 (레이어 캐시를 위해 소스보다 먼저) ──
# ml/golden_set/requirements.txt가 -r ../../indexer/util/requirements.txt를
# 참조하므로 그 파일도 같은 상대 위치에 있어야 한다.
COPY indexer/util/requirements.txt /app/indexer/util/requirements.txt
COPY image-processor/requirements.txt /app/image-processor/requirements.txt
COPY ml/golden_set/requirements.txt /app/ml/golden_set/requirements.txt
RUN pip install --no-cache-dir -r /app/image-processor/requirements.txt \
 && pip install --no-cache-dir -r /app/ml/golden_set/requirements.txt

# ── 소스 ──
COPY image-processor/config.py /app/image-processor/config.py
COPY image-processor/pipeline/ /app/image-processor/pipeline/
COPY image-processor/services/ /app/image-processor/services/
COPY ml/golden_set/ /app/ml/golden_set/

# `python -m ml.golden_set.runner`가 /app을, `import config`(image-processor
# 내부)가 /app/image-processor를 각각 찾게 한다. golden_set은 `.config`를
# 상대 import로 쓰므로 이름이 겹치지 않는다.
ENV PYTHONPATH=/app:/app/image-processor \
    GOLDEN_IMAGE_PROCESSOR_PATH=/app/image-processor \
    GOLDEN_RUN_ROOT=/app/runs \
    HF_HOME=/app/.cache/huggingface \
    PYTHONUNBUFFERED=1

# 확인용 웹의 컨테이너 내부 포트. 호스트 매핑은 compose가 정한다.
EXPOSE 8081

# 기본은 배치 러너다. 확인용 웹은 compose에서 command를 덮어쓴다
# (docker-compose.golden_set.yml의 golden-set-web).
CMD ["python", "-m", "ml.golden_set.runner"]
