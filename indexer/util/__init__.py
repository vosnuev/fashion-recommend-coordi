"""indexer 컨테이너들이 공유하는 유틸리티 패키지.

product_indexer(운영)와 old(ETRI 레거시) 양쪽 이미지가 함께 COPY 하므로
특정 패키지의 설정 모듈(config / product_config)에 의존하지 않는다.
"""
