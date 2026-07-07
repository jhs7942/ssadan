"""Gemini API를 이용한 10층 식단 이미지 파싱 모듈 (구조화 JSON 출력 기반)

이미지에서 Markdown 테이블을 받아 문자열로 파싱하던 방식은 다음 문제가 있었다.
  - `|` 분리 기반 파싱이라 메뉴에 특수문자가 섞이면 깨진다.
  - 컬럼 위치로 날짜를 매핑해 이미지 날짜가 밀리면 엉뚱한 날짜에 배정된다.
  - 코너명 문자열이 조금만 달라도 행 전체가 누락된다.

이를 해결하기 위해 Gemini의 `response_schema`(구조화 출력)로 JSON을 강제하고,
요일(월~금)을 키로 삼아 그 주의 실제 날짜에 안전하게 매핑한다.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from google import genai
from google.genai import types


FLOOR_10_COURSES = ["10F 공존 (도시락)", "10F 공존 (브런치)", "10F 공존 (샐러드)"]
KST = timezone(timedelta(hours=9))

_WEEKDAY_NAMES = ["월", "화", "수", "목", "금"]
_MODEL = "gemini-2.5-flash"
_MAX_RETRIES = 3

# 스키마 코스 키 → (출력 코너명, 항목 구분자)
# 도시락은 쉼표로, 브런치/샐러드는 ' & '로 연결한다(기존 db/*.md 표기 규칙 유지).
_COURSE_MAP = {
    "dosirak": ("10F 공존 (도시락)", ", "),
    "brunch": ("10F 공존 (브런치)", ", "),
    "salad": ("10F 공존 (샐러드)", ", "),
}

# 이미지에서 브런치/샐러드 항목이 '& 메뉴'처럼 구분자를 달고 추출되는 경우가 있어 앞의 &를 제거
_LEADING_SEP = re.compile(r"^\s*[&＆]\s*")


def _clean_item(raw) -> str:
    """메뉴 항목 앞에 붙은 구분자(&)와 공백 제거"""
    return _LEADING_SEP.sub("", str(raw)).strip()

_PROMPT = (
    "제공된 이미지는 SSAFY 멀티캠퍼스 10층 공존식당의 주간(월~금) 식단표입니다.\n"
    "각 요일별로 도시락 / 브런치 / 샐러드 코너의 메뉴를 항목 단위로 추출하세요.\n\n"
    "규칙:\n"
    "- weekday는 월, 화, 수, 목, 금 중 하나로 표기하세요.\n"
    "- date는 이미지에 표시된 날짜를 그대로 적으세요 (예: '3.2'). 표시가 없으면 빈 문자열.\n"
    "- 밥, 국, 반찬 등 각 메뉴를 개별 문자열로 분리해 배열에 담으세요.\n"
    "- 메뉴명 앞에 붙은 '&' 같은 구분 기호는 빼고 순수 메뉴명만 담으세요.\n"
    "- 해당 요일/코너에 메뉴가 없으면 빈 배열([])로 두세요.\n"
    "- 특정 요일이 '미운영'/'휴무'/'휴일' 등 운영하지 않으면 status에 그 사유를 적고 메뉴 배열은 모두 비워두세요. 정상 영업이면 status는 빈 문자열.\n"
    "- 이미지에 있는 글자만 그대로 정확히 옮기고, 없는 메뉴를 지어내지 마세요."
)

# Gemini 구조화 출력 스키마: {"days": [{weekday, date, dosirak[], brunch[], salad[]}]}
_MENU_ITEMS = types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING))
_RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "days": types.Schema(
            type=types.Type.ARRAY,
            description="월요일부터 금요일까지 5일치 식단",
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "weekday": types.Schema(
                        type=types.Type.STRING,
                        enum=_WEEKDAY_NAMES,
                        description="요일",
                    ),
                    "date": types.Schema(
                        type=types.Type.STRING,
                        description="이미지에 표시된 날짜(예: '3.2'), 없으면 빈 문자열",
                    ),
                    "status": types.Schema(
                        type=types.Type.STRING,
                        description="미운영/휴무면 사유(예: '미운영'), 정상 영업이면 빈 문자열",
                    ),
                    "dosirak": _MENU_ITEMS,
                    "brunch": _MENU_ITEMS,
                    "salad": _MENU_ITEMS,
                },
                required=["weekday", "dosirak", "brunch", "salad"],
            ),
        )
    },
    required=["days"],
)


def parse_floor10_image(image_path: str, reference_date: datetime = None) -> Dict[str, Dict[str, str]]:
    """
    Gemini 구조화 출력으로 10층 식단 이미지를 파싱.

    미완성 항목이 있으면 최대 _MAX_RETRIES회까지 재시도하며 성공 항목을 누적한다.
    (공휴일 등으로 더 이상 채워지지 않으면 조기 종료)

    Args:
        image_path: 이미지 파일 경로
        reference_date: 해당 주의 임의 날짜 (KST). None이면 오늘 기준 계산

    Returns:
        Dict[날짜 문자열 (YYYY-MM-DD), Dict[코너명, 메뉴 내용]]

    Raises:
        ValueError: GEMINI_API_KEY 미설정 또는 유효한 데이터를 끝내 얻지 못한 경우
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")

    client = genai.Client(api_key=api_key)
    image_part = _load_image_part(image_path)

    week_dates = _week_dates(reference_date)  # 월~금 ["YYYY-MM-DD", ...]
    weekday_to_date = dict(zip(_WEEKDAY_NAMES, week_dates))
    total_expected = len(week_dates) * len(FLOOR_10_COURSES)

    accumulated: Dict[str, Dict[str, str]] = {}

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            days = _request_menu(client, image_part)
        except Exception as e:
            print(f"⚠️  Gemini 호출/파싱 실패 (retry {attempt}/{_MAX_RETRIES}): {e}")
            if attempt < _MAX_RETRIES:
                time.sleep(min(2 ** attempt, 8))
            continue

        before = _filled_count(accumulated, week_dates)
        _accumulate(accumulated, days, weekday_to_date, week_dates)
        filled = _filled_count(accumulated, week_dates)

        if filled >= total_expected:
            if attempt > 1:
                print(f"✓ {attempt}회 시도 후 모든 10층 식단 파싱 완료")
            break

        # 재시도했는데도 새로 채워진 항목이 없으면(예: 공휴일) 더 시도하지 않음
        if attempt > 1 and filled == before:
            break

        if attempt < _MAX_RETRIES:
            print(f"⚠️  미완성 항목 {total_expected - filled}개, retry {attempt}/{_MAX_RETRIES}...")

    if not accumulated:
        raise ValueError("Gemini 파싱 실패: 최대 재시도 후에도 유효한 10층 식단 데이터가 없습니다.")

    return accumulated


def _load_image_part(image_path: str) -> types.Part:
    """이미지 파일을 Gemini 입력 Part로 로드"""
    with open(image_path, "rb") as f:
        image_data = f.read()
    ext = image_path.rsplit(".", 1)[-1].lower()
    mime_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
    return types.Part.from_bytes(data=image_data, mime_type=mime_type)


def _request_menu(client: "genai.Client", image_part: types.Part) -> List[dict]:
    """Gemini에 이미지를 보내 구조화 JSON(days 리스트)을 받아 반환"""
    response = client.models.generate_content(
        model=_MODEL,
        contents=[image_part, _PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
            temperature=0,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini 응답이 비어 있습니다.")
    data = json.loads(text)  # JSONDecodeError는 ValueError의 하위 클래스라 상위에서 잡힌다
    days = data.get("days", []) if isinstance(data, dict) else []
    if not isinstance(days, list):
        raise ValueError("Gemini 응답의 'days'가 리스트가 아닙니다.")
    return days


def _accumulate(
    accumulated: Dict[str, Dict[str, str]],
    days: List[dict],
    weekday_to_date: Dict[str, str],
    week_dates: List[str],
) -> None:
    """파싱된 days를 날짜별 코너 메뉴로 누적 (이미 채워진 항목은 유지)"""
    for day in days:
        if not isinstance(day, dict):
            continue
        date_str = _resolve_date(day, weekday_to_date, week_dates)
        if not date_str:
            continue
        slot = accumulated.setdefault(date_str, {})
        status = str(day.get("status", "")).strip()  # 미운영/휴무 등
        for key, (course_name, separator) in _COURSE_MAP.items():
            if course_name in slot:
                continue
            items = [c for c in (_clean_item(x) for x in day.get(key, [])) if c]
            if items:
                slot[course_name] = separator.join(items)
            elif status:
                # 메뉴가 없고 운영 안 하는 날이면 사유(미운영 등)로 채움
                slot[course_name] = status


def _resolve_date(day: dict, weekday_to_date: Dict[str, str], week_dates: List[str]) -> str:
    """요일(우선) 또는 이미지 날짜(보조)로 그 주의 실제 날짜(YYYY-MM-DD)를 결정"""
    weekday = str(day.get("weekday", "")).strip()
    if weekday in weekday_to_date:
        return weekday_to_date[weekday]

    # 요일 누락 시: 이미지에 표시된 'M.D' 형태 날짜를 이번 주 날짜에 매칭
    raw = str(day.get("date", "")).replace("/", ".").replace("-", ".")
    parts = [p for p in raw.split(".") if p.strip().isdigit()]
    if len(parts) >= 2:
        month, dom = int(parts[-2]), int(parts[-1])
        for ds in week_dates:
            dt = datetime.strptime(ds, "%Y-%m-%d")
            if dt.month == month and dt.day == dom:
                return ds
    return ""


def _filled_count(accumulated: Dict[str, Dict[str, str]], week_dates: List[str]) -> int:
    """이번 주 날짜 범위에서 채워진 (날짜×코너) 항목 수"""
    return sum(len(accumulated.get(d, {})) for d in week_dates)


def _week_dates(reference: datetime = None) -> List[str]:
    """해당 주 월~금 날짜 문자열(YYYY-MM-DD) 리스트 반환 (KST 기준)"""
    if reference is None:
        reference = datetime.now(KST)
    monday = reference - timedelta(days=reference.weekday())
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]
