# Dev_discord_bot
개발 과정속에 함께 사용할 수 있는 시간 기록, 대시보드 디스코드 봇 입니다. 
프로젝트/태그/메모로 개발 시간을 기록하고, 뽀모도로, 리더보드, 수동 기록, CSV 추출을 지원하는 슬래시 커맨드 기반 봇입니다.
Presence/메시지내용 인텐트 없이 동작하며, SQLite 하나로 가볍게 운영됩니다.

주요 기능

타이머 기록: /start → /stop (프로젝트·태그·메모)
뽀모도로: 집중/휴식/사이클, work 구간 자동 세션 기록
리더보드: 기간 상위 활동 시간 차트 PNG /summary
수동 기록: 깜빡한 시간 분 단위로 추가 /log
CSV 추출: 기간 세션 내보내기 /export_csv

빠른 시작

1) 요구사항
Python 3.10+
pip install -U discord.py aiosqlite matplotlib

2) 환경변수
# 필수
export DISCORD_TOKEN="디스코드_봇_토큰"

# 선택 (기본: dev_timer.db)
export ACTIVITY_DB="경로/파일명.db"

3) 실행
python bot.py

권한 & 인텐트

OAuth2 스코프: bot, applications.commands
서버 권한: View Channels, Send Messages, Attach Files, (권장) Embed Links
게이트웨이 인텐트: 기본값으로 충분 (Presence/Message Content 불필요)

사용 예시 (슬래시 커맨드)

시작:
/start project:web-app tags:frontend,refactor note:컴포넌트 정리

종료:
/stop note:PR 올림

상태:
/status

리더보드:
/summary days:7 top_n:10 project:web-app

수동 기록:
/log minutes:90 project:web-app tags:bugfix note:핫픽스 when:2025-10-29 10:00

CSV 추출:
/export_csv days:30 project:web-app

뽀모도로:
/pomodoro work_min:50 break_min:10 cycles:2 project:web-app tags:focus

데이터 모델 (SQLite)

work_sessions
guild_id, user_id, project, tags, note, start_ts, end_ts(NULL 진행중), source(timer|manual|pomodoro)

pomodoro
work_min, break_min, cycles, started_ts, active(1/0), project, tags

시간은 UTC epoch로 저장합니다.

설계 포인트

진행 중 세션은 end_ts=NULL → /stop 또는 뽀모도로 워커가 종료
합계/리더보드는 기간과 교차하는 구간만 정확히 합산
PNG 차트는 matplotlib로 생성하여 업로드

커스터마이징 아이디어

역할 제한(예: 운영진만 /export_csv)
주/일 단위 버킷 차트
프로젝트/태그 자동 제안(최근 사용 캐시)
