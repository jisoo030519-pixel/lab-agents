---
description: 학회 프로그램북에서 우리 연구실 사람들의 발표 일정을 뽑아 정리한다
argument-hint: [프로그램북 PDF 경로 또는 URL]
---

프로그램북에서 연구실 구성원의 발표 일정을 추출한다. 대상: $ARGUMENTS

## 1. 프로그램북 확보

- 인자가 로컬 PDF 경로면 그대로 사용한다.
- URL이면 내려받아 `programs/` 에 저장한다.
- 인자가 없으면 `targets.yaml`의 학회 사이트에서 프로그램북을 찾는다.
  못 찾으면 어디까지 찾아봤는지 밝히고 사용자에게 링크를 요청한다.

## 2. 우리 사람 찾기

```
python watch.py scan-pdf <PDF 경로>
```

이 명령은 `members.yaml`의 모든 표기(`aliases`)를 공백·대소문자 무시하고 대조해
**구성원이 등장하는 페이지만** `scans/<이름>.hits.txt` 로 뽑아준다.
수백 쪽짜리 프로그램북을 전부 읽지 말고 이 파일만 읽는다.

한 명도 안 잡히면 원인을 구분해서 보고한다.

- `aliases` 표기 문제 → 프로그램에 실린 실제 표기를 확인해 `members.yaml` 수정을 제안
- PDF가 스캔 이미지 → 텍스트가 없으므로 OCR 필요. 이 경우 **추측하지 말고** 사용자에게 알린다.

## 3. 발표 일정 구조화

`scans/*.hits.txt` 를 읽고 각 발표를 항목으로 만든다.

```json
[
  {"society":"한국화학공학회","event":"2026 가을 학술대회","member":"홍길동",
   "title":"LFP 양극재 전주기 비용 분석","kind":"oral",
   "start":"2026-10-22T14:30","end":"2026-10-22T14:50",
   "room":"A홀","session":"이차전지 소재 II","note":"발표번호 O-234"}
]
```

**연구실 구성원이 제1저자인 발표만 기록한다.** 우리 사람 이름이 보여도 제1저자가
아니면 넣지 않는다. 남의 논문에 공저자로 들어간 건은 우리 랩 일정이 아니다.
`upsert-sessions` 가 명단 밖 제1저자를 거부하므로 우회하려 하지 말 것.

- `kind`: `oral` / `poster` / `invited` / `keynote` / `chair`
- `member`는 그 발표의 **제1저자**이며, `members.yaml`의 `name` 값과 똑같이 쓴다.
  프로그램에 영문으로 실렸어도 한글 표시 이름으로 바꿔 적는다.
- **공저자 목록은 `note`에 적지 않는다.** 비고에는 발표번호, 수상 후보 여부,
  포스터 부착·철거 시간 같은 실무 정보만 남긴다.
- `start`는 `YYYY-MM-DDTHH:MM`. 시각을 못 찾으면 날짜만(`YYYY-MM-DD`) 넣는다.
  **날짜와 시각을 지어내지 않는다.**
- 발표번호·좌장 여부처럼 프로그램에만 있는 정보는 `note`에 남긴다.
- **동명이인은 소속으로 검증한다.** 이름이 같아도 소속이 우리 것이 아니면 남이다.
  실제 사례: KIChE 의 `이승훈`은 단국대, NAT 의 여러 `Jong Woo Kim`은 KAERI 소속이었다.
- 이름 검색이 놓칠 수 있으니 `scan-pdf` 의 **소속 기준 결과("소속만 잡힌 쪽")를 반드시 확인한다.**
  실제로 NAT 2026 에서 로마자 표기가 달라(`Hyeon`/`Hun`) 이름 검색이 두 명을 놓쳤고,
  소속 검색이 찾아냈다.

```
python watch.py upsert-sessions <found.json 경로>
python watch.py report
python watch.py ics
python watch.py sync
```

## 4. 보고

1. 사람별로 묶어서 — "홍길동: 10/22(수) 14:30 구두, A홀" 형태
2. 이름이 잡혔는데 일정 정보가 불완전한 항목은 따로 표시
3. `members.yaml`에 없는 이름이 나왔으면 (upsert가 ⚠️로 알려준다) 명단 추가 여부 확인
4. `LATEST.md` 와 각자의 `calendars/이름.ics` 를 SendUserFile로 첨부
5. `python watch.py sync` 후 같은 파일 경로로 Artifact 재퍼블리시
