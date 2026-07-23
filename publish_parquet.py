"""기존 실행 파일 호환용.

Parquet를 main에 커밋하지 않고 data-latest 단일 스냅샷으로 게시한다.
"""

from publish_data_snapshot import main


if __name__ == "__main__":
    raise SystemExit(main())
