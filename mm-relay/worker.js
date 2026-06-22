/**
 * Mattermost → GitHub Actions 중계 함수 (Cloudflare Worker)
 *
 * Mattermost Slash Command 가 보낸 요청을 검증한 뒤,
 * GitHub repository_dispatch API 를 호출해 워크플로를 실행한다.
 *
 * 필요한 Secret (wrangler secret put 으로 등록):
 *   - GITHUB_TOKEN : 대상 레포에 Contents/Actions write 권한이 있는 Fine-grained PAT
 *   - MM_TOKEN     : Mattermost Slash Command 생성 시 발급된 검증 토큰
 *
 * 환경변수 (wrangler.toml 의 [vars]):
 *   - GITHUB_REPO  : "owner/repo" 형식 (예: hanlyang0522/ssadan)
 *
 * 사용 예 (Mattermost):
 *   /식단 daily              → 오늘 점심 알림 발송
 *   /식단 daily 2026-01-15   → 특정 날짜 점심 알림 발송
 *   /식단 crawl              → 주간 식단 재크롤링
 */

const HELP = "사용법: `/식단 daily [YYYY-MM-DD]` 또는 `/식단 crawl`";

function reply(text, type = "ephemeral") {
  // ephemeral: 명령을 친 본인에게만 보임 / in_channel: 채널 전체 공개
  return Response.json({ response_type: type, text });
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    // Mattermost Slash Command 는 application/x-www-form-urlencoded 로 전송
    let form;
    try {
      form = await request.formData();
    } catch {
      return new Response("Bad Request", { status: 400 });
    }

    // 1) Mattermost 검증 토큰 확인
    if (form.get("token") !== env.MM_TOKEN) {
      return new Response("Unauthorized", { status: 401 });
    }

    // 2) 명령 인자 파싱: 첫 단어가 액션, 두 번째 단어가 날짜(선택)
    const text = (form.get("text") || "").trim();
    const [action, dateArg] = text.split(/\s+/);

    let eventType;
    const payload = {};

    if (action === "crawl") {
      eventType = "mm-crawl";
    } else if (action === "daily" || action === "") {
      eventType = "mm-daily";
      if (dateArg) {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(dateArg)) {
          return reply(`❌ 날짜 형식 오류: \`${dateArg}\` (YYYY-MM-DD 형식이어야 합니다)`);
        }
        payload.date = dateArg;
      }
    } else {
      return reply(`❓ 알 수 없는 명령: \`${action}\`\n${HELP}`);
    }

    // 3) GitHub repository_dispatch 호출
    const ghResponse = await fetch(
      `https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "mm-relay",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ event_type: eventType, client_payload: payload }),
      }
    );

    if (ghResponse.ok) {
      const label = eventType === "mm-crawl" ? "주간 식단 크롤링" : "점심 식단 알림";
      const dateInfo = payload.date ? ` (${payload.date})` : "";
      return reply(`✅ ${label}${dateInfo} 실행을 요청했습니다. Actions 탭에서 진행 상황을 확인하세요.`);
    }

    const errBody = await ghResponse.text();
    return reply(`❌ GitHub 호출 실패 (HTTP ${ghResponse.status})\n\`\`\`\n${errBody}\n\`\`\``);
  },
};
