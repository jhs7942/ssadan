# images/ — 10층 주간 식단 이미지

이 폴더에 **10층 공존 주간 식단표 이미지(PNG/JPG)를 커밋**하면,
[parse_image.yml](../.github/workflows/parse_image.yml) 워크플로우가 자동으로:

1. 커밋된 이미지를 Gemini로 파싱
2. 이번 주 `db/YYYY-MM-DD.md`의 **10층 식단**에 병합 (20층은 Welstory API로 갱신)
3. **오늘 점심 식단**을 Mattermost/Discord 웹훅으로 발송

## 사용법

```bash
# 이번 주 10층 식단 이미지를 추가하고 커밋/푸시
git add images/2026-07-06.png
git commit -m "Add 10F menu image for 2026-07-06"
git push
```

## 참고

- **이번 주**에 해당하는 이미지를 올리세요. 크롤 기준 날짜는 커밋 시점(KST)의 그 주(월~금)입니다.
- 한 번에 한 장이면 충분합니다. 이번 push에서 바뀐 이미지를 우선 파싱합니다.
- 파일명은 자유지만 `YYYY-MM-DD.png`처럼 주차를 알아볼 수 있게 권장합니다.
- 이미지는 삭제되지 않고 그대로 남습니다(다음 주엔 새 이미지를 추가).
- 이 방식을 쓰면 10층용 Mattermost 로그인 시크릿(`MM_LOGIN_JSON` 등)은 없어도 됩니다.
  단, 20층 갱신을 위해 `WELSTORY_*`와 파싱용 `GEMINI_API_KEY`는 필요합니다.
- 모든 코너(도시락/브런치/샐러드) 메뉴는 쉼표(,)로 구분되어 출력됩니다. 이미지에 `& 메뉴`로 적혀 있어도 앞의 `&`는 자동 정리됩니다.

## 수동 재실행

시크릿(웰스토리 계정 등)을 갱신한 뒤 다시 돌리려면:

- GitHub → Actions → **Parse 10F Image and Notify** → **Run workflow**(수동 실행) 또는 이전 run → **Re-run all jobs**, 또는
- 이 폴더의 파일에 사소한 변경을 커밋해 다시 트리거
