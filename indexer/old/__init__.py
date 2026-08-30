"""ETRI 패션 코디 데이터셋(11번) 적재 레거시 패키지 (Dockerfile.indexer.old).

운영 파이프라인은 product_indexer 패키지로 이동했다. 이 패키지는 이전
indexer 컨테이너(Dockerfile.indexer)가 쓰던 기능만 그대로 보존한다.

실행 진입점:
    python -m old.fashion_indexer
"""
