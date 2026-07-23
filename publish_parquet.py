"""기존 실행 파일 호환용.

Parquet를 Git 이력에 넣지 않고 web-data-latest Release 자산으로 게시한다.
"""

from publish_data_snapshot import main


if __name__ == "__main__":
    raise SystemExit(main())
