# 패킷 웹 데이터

`build_packet_web_data.py`는 최신 `market_active.sqlite`와
`market_completed.sqlite`에서 장비만 추출해 Streamlit용 파일을 만듭니다.

```powershell
python build_packet_web_data.py
```

생성 파일:

- `packet_active.parquet`
- `packet_completed.parquet`
- `item.xlsx`

기본 입력 경로는 저장소의 상위 폴더입니다. 다른 위치라면
`--itemp`, `--gem-prices`, `--active-db`, `--completed-db`,
`--output-dir` 인수를 지정할 수 있습니다.

## 검색 규칙

- 양수 일반 조건은 총스탯 기준입니다.
- `법신 n`만 추가 인트+럭+마력 합 기준입니다.
- `힘0`, `덱0`, `공0` 같은 0 조건은 해당 추가스탯이 0이라는 뜻입니다.
- `법사/법지/법행` 합은 인트+럭입니다.
- 패킷 보석 스탯은 총스탯에 포함되지 않으며 검색 스탯에도 더하지 않습니다.

## 보석 가격

```text
보석비 원가
= 보석 평균가 합
 기본 가공비 225만
 등급별 가공비

찐판매가
= 판매가 - 보석비 원가 × 90%
```

등급별 가공비는 하급 75만, 중급 150만, 상급 225만입니다.

웹 화면에서는 모든 가격을 10,000 메소 단위로 반올림해 `(만)` 열로 표시합니다.

## 날짜

- Active/Completed 모두 웹 표시와 정렬에는 패킷을 실제로 수집한 `packet_time`(KST)을 사용합니다.
- Active↔Completed의 3일 중복 판정에는 패킷 행 내부의 `internal_time`을 사용합니다.
- `captured_at`에는 패킷 수집시각을, `event_time`에는 기존 상태별 시각을 별도로 보존합니다.

## Active/Completed 중복

다음 값이 같고 Active 수집 후 3일 안에 Completed가 있으면
Completed만 남깁니다.

```text
itemCode + 수량 + 총가격 + 업횟 + 작횟
+ 실제 장비 스탯 + 옵션코드
```

`stats30`의 내부 제어 필드는 중복 지문에 사용하지 않습니다.
