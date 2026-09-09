# FC쏘아 · FCSA

FC쏘아의 선수단, 경기 기록, 유니폼, YouTube 영상을 소개하는 홈페이지입니다.

- 홈페이지 주소: https://kyuuu410.github.io/fcsa/
- YouTube: https://www.youtube.com/@FC%EC%8F%98%EC%95%84
- 운영·기록 반영 안내: [디자인안내.md](디자인안내.md)
- 비공개 시트 연결·자동 갱신: [기록자동반영.md](기록자동반영.md)

## 배포와 영상 갱신

GitHub Pages와 GitHub Actions를 사용합니다. 기본 브랜치 변경, 매시간 예약 실행, 수동 실행으로 공개 YouTube 피드를 확인하고 최신 영상 4개를 반영합니다. PC가 꺼져 있어도 GitHub에서 실행됩니다.

YouTube 조회에 실패하면 기존 공개 페이지를 유지합니다. 새 시트 기록을 배포하는 실행에서는 현재 공개된 영상 목록을 검증해 유지하고 기록을 반영합니다. 첫 배포 또는 수동 복구 시에는 Actions의 `use_saved_videos`를 선택해 저장된 영상 목록으로 배포할 수 있습니다. 마지막 정상 영상 확인 시각은 그대로 유지합니다.

예약 실행은 GitHub 상황에 따라 늦어질 수 있습니다. 공개 저장소에 60일 동안 활동이 없으면 예약 실행이 비활성화될 수 있어 Actions에서 재활성화가 필요합니다.

## 경기 기록

비공개 원본 파일의 인증을 연결하면 **2026년 9월 12일 22시부터 매일 22시(한국시간)**에 변경을 확인합니다. 변경된 개인·팀 기록만 검증 후 저장·배포합니다. 유튜브의 매시간 실행에서는 시트를 조회하지 않습니다. PC를 켜 둘 필요는 없습니다.

원본 Excel의 값·수식·서식·권한은 수정하지 않습니다. 원본 링크·식별자·Excel·비고의 개인 메모·인증키는 공개 저장소에 포함하지 않습니다. 인스타그램 경기별 최종 점수의 자동 수집은 별도이며 이번 시트 동기화에 포함되지 않습니다. [연결에 필요한 두 Secret과 운영 방법](기록자동반영.md)을 먼저 확인하세요. 인증이 없으면 자동 반영은 시작되지 않습니다.

## 로컬 실행과 검증

Python 3.12 이상에서 다음 명령을 실행합니다.

```powershell
python -B tools/serve.py
python -B -m unittest discover -s tools -p 'test_update_videos.py'
```

웹사이트와 영상 갱신 도구는 추가 패키지가 필요 없습니다. 시트 자동 갱신은 `tools/requirements-records.txt`의 `openpyxl`과 `google-auth[requests]`를 사용합니다. 인증 없이 자동화 코드를 검증하려면 의존성 설치 후 `python -B -m unittest discover -s tools -p 'test_*.py'`를 실행합니다.
