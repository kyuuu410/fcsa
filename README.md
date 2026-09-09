# FC쏘아 · FCSA

FC쏘아의 선수단, 경기 기록, 유니폼, YouTube 영상을 소개하는 홈페이지입니다.

- 홈페이지 주소: https://kyuuu410.github.io/fcsa/
- YouTube: https://www.youtube.com/@FC%EC%8F%98%EC%95%84
- 운영·기록 반영 안내: [디자인안내.md](디자인안내.md)

## 배포와 영상 갱신

GitHub Pages와 GitHub Actions를 사용합니다. 기본 브랜치 변경, 매시간 예약 실행, 수동 실행으로 공개 YouTube 피드를 확인하고 최신 영상 4개를 반영합니다. PC가 꺼져 있어도 GitHub에서 실행됩니다.

YouTube 조회에 실패하면 기존 공개 페이지를 유지합니다. 첫 배포 또는 수동 복구 시에만 Actions의 `use_saved_videos`를 선택해 저장된 영상 목록으로 배포할 수 있습니다. 이 선택은 최신 영상 조회를 생략하며 마지막 정상 확인 시각은 그대로 유지합니다.

예약 실행은 GitHub 상황에 따라 늦어질 수 있습니다. 공개 저장소에 60일 동안 활동이 없으면 예약 실행이 비활성화될 수 있어 Actions에서 재활성화가 필요합니다.

## 경기 기록

현재 경기·선수 기록은 2026년 9월 9일 반영한 팀 관리 원본 문서의 스냅샷입니다. 원본 문서의 링크와 식별자는 공개 저장소에 포함하지 않으며, Google 문서 수정이 자동 반영되지는 않습니다. 원본 Excel의 값·수식·서식·권한을 변경하지 않고 읽기 전용으로 변환합니다. 원본 파일과 비고의 개인 메모는 저장소에 포함하지 않습니다.

## 로컬 실행과 검증

Python 3.12 이상에서 다음 명령을 실행합니다.

```powershell
python -B tools/serve.py
python -B -m unittest discover -s tools -p 'test_update_videos.py'
```

웹사이트와 영상 갱신 도구는 추가 패키지가 필요 없습니다. 기록을 새로 변환할 때만 `openpyxl`이 필요합니다.
