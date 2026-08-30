"""네이버·11번가 쇼핑 상품 임베딩 worker 패키지 (Dockerfile.product-indexer).

실행 진입점:
    python -m product_indexer.product_indexer_api   # drain 트리거 HTTP API
    python -m product_indexer.product_indexer       # worker 직접 실행
"""
