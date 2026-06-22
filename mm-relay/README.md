# Mattermost → GitHub Actions 중계 (mm-relay)

Mattermost Slash Command 로 GitHub Actions 워크플로를 실행하기 위한 중계 함수입니다.

```
[Mattermost /식단] ──POST──> [Cloudflare Worker] ──repository_dispatch──> [GitHub Actions]
```

Mattermost 의 Outgoing Webhook/Slash Command 는 커스텀 헤더를 보낼 수 없어 GitHub API 를 직접 호출할 수 없습니다. 그래서 인증 헤더를 붙여 GitHub 를 호출해 주는 얇은 중계가 필요합니다.

## 트리거되는 워크플로

| 명령 | event_type | 실행 워크플로 |
| :--- | :--- | :--- |
| `/식단 daily [YYYY-MM-DD]` | `mm-daily` | [daily_notify.yml](../.github/workflows/daily_notify.yml) |
| `/식단 crawl` | `mm-crawl` | [weekly_crawl.yml](../.github/workflows/weekly_crawl.yml) |

## 배포 (Cloudflare Workers, 무료)

> Vercel / AWS Lambda 로도 동일 로직을 옮길 수 있습니다. 핵심은 `worker.js` 의 검증 + dispatch 호출 부분입니다.

### 1. GitHub Fine-grained PAT 발급

GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token

- **Repository access**: `hanlyang0522/ssadan` 만 선택
- **Permissions**: `Contents: Read and write`, `Actions: Read and write`

### 2. Worker 배포 및 Secret 등록

```bash
cd mm-relay
npx wrangler login        # 최초 1회
npx wrangler secret put GITHUB_TOKEN   # 1단계에서 발급한 PAT 입력
npx wrangler secret put MM_TOKEN       # 4단계에서 발급될 Mattermost 토큰 (먼저 임시값 넣고 나중에 갱신 가능)
npx wrangler deploy
```

배포가 끝나면 `https://ssadan-mm-relay.<계정>.workers.dev` 형태의 URL 이 출력됩니다.

### 3. (선택) GITHUB_REPO 변경

레포 이름이 다르면 [wrangler.toml](wrangler.toml) 의 `GITHUB_REPO` 값을 수정하세요.

### 4. Mattermost Slash Command 등록

Mattermost → Integrations → Slash Commands → Add Slash Command

- **Command Trigger Word**: `식단`
- **Request URL**: 2단계에서 받은 Worker URL
- **Request Method**: `POST`
- **Autocomplete Hint**: `daily [YYYY-MM-DD] | crawl`

저장 시 발급되는 **Token** 을 `wrangler secret put MM_TOKEN` 으로 등록(또는 갱신)합니다.

## 사용 예

```
/식단 daily              → 오늘 점심 알림 발송
/식단 daily 2026-01-15   → 특정 날짜 점심 알림 발송
/식단 crawl              → 주간 식단 재크롤링
```
