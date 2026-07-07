"""SSAFY 식단 알림 봇 - CLI 진입점"""
import argparse
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from notification_sender import NotificationSender
from welstory_crawler import WelstoryCrawler


# 요일 이름 상수
WEEKDAY_NAMES_KR = ['월', '화', '수', '목', '금', '토', '일']


def crawl_weekly(db_path: str = "db", floor10_image: str = None) -> bool:
    """
    Welstory Plus API에서 이번 주 20층 식단을 가져와 Markdown 파일로 저장.
    floor10_image가 있으면 그 이미지로 10층을 파싱해 병합하고,
    없으면(정기 크롤) 기존 파일의 10층을 그대로 보존한다.

    Args:
        db_path: Markdown 파일 저장 경로
        floor10_image: 10층 식단 이미지 경로. 지정 시 이 이미지로 10층을 파싱.

    Returns:
        성공 여부
    """
    print("=" * 60)
    print("🔄 Welstory Plus API 식단 크롤링 시작")
    print("=" * 60)

    crawler = WelstoryCrawler()

    print("\n1️⃣  주간 식단 조회 중...")
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst)
    days_since_monday = today.weekday()
    monday = today - timedelta(days=days_since_monday)

    meal_data = crawler.fetch_weekly_meal_data(today)
    if not meal_data or not any(meal_data[d] for d in meal_data):
        print("✗ 식단 크롤링 실패")
        return False

    print(f"✓ {len(meal_data)}개 날짜의 식단 조회 완료")

    print("\n2️⃣  10층 식단 처리 중...")
    if floor10_image:
        # 이미지 커밋 경로: 커밋된 이미지에서 10층 파싱해 병합
        _try_fetch_floor10(crawler, meal_data, today, image_path=floor10_image)
    else:
        # 정기 크롤 경로: 20층만 갱신하고 기존 파일의 10층은 그대로 보존
        _preserve_existing_floor10(crawler, meal_data, db_path, monday)

    print("\n3️⃣  Markdown 변환 및 저장 중...")
    markdown = crawler.convert_to_markdown(meal_data)
    monday_str = monday.strftime("%Y-%m-%d")
    file_path = crawler.save_to_file(markdown, monday_str, db_path)

    print(f"✓ 파일 저장: {file_path}")
    return True


def _try_fetch_floor10(crawler, meal_data: dict, reference_date, image_path: str = None) -> None:
    """
    10층 식단 이미지를 Gemini로 파싱해 meal_data에 병합.
    image_path가 주어지면 그 이미지를, 없으면 Mattermost에서 자동 수집한 이미지를 사용.
    실패 시 경고 로그만 출력하고 계속 진행(10층 placeholder 유지).
    """
    if not os.environ.get("GEMINI_API_KEY"):
        print("⚠️  GEMINI_API_KEY 미설정, 10층 placeholder 유지")
        return

    try:
        from ten_floor_parser import parse_floor10_image

        if image_path:
            # 1) 커밋/전달된 로컬 이미지 사용
            if not os.path.exists(image_path):
                print(f"⚠️  이미지 파일을 찾을 수 없음: {image_path}, placeholder 유지")
                return
            print(f"  🖼️  로컬 이미지 파싱: {image_path}")
        else:
            # 2) Mattermost 채널에서 자동 수집
            required_vars = ["MATTERMOST_BASE_URL", "MATTERMOST_CHANNEL_ID", "MM_LOGIN_JSON"]
            missing = [v for v in required_vars if not os.environ.get(v)]
            if missing:
                print(f"⚠️  10층 수집 환경변수 미설정 ({', '.join(missing)}), placeholder 유지")
                return
            from mm_image_fetcher import MattermostImageFetcher

            tmp_dir = tempfile.mkdtemp()
            fetcher = MattermostImageFetcher()
            image_path = fetcher.fetch_floor10_image(dest_dir=tmp_dir)

        floor10_data = parse_floor10_image(image_path, reference_date=reference_date)
        crawler.merge_floor10_data(meal_data, floor10_data)
        print("✓ 10층 식단 병합 완료")
    except Exception as e:
        print(f"⚠️  10층 식단 수집/파싱 실패: {e}")
        print("   → 10층 placeholder 유지하고 계속 진행합니다.")


def _preserve_existing_floor10(crawler, meal_data: dict, db_path: str, monday) -> None:
    """
    정기 크롤 시 기존 db 파일의 10층 식단을 읽어 보존 (20층만 갱신, 10층은 미변경).
    10층은 이미지 커밋(parse_image)으로만 갱신되므로, 정기 크롤이 placeholder로 덮어쓰지 않게 한다.
    """
    monday_str = monday.strftime("%Y-%m-%d")
    file_path = os.path.join(db_path, f"{monday_str}.md")
    week_dates = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]

    existing = crawler.load_floor10_from_markdown(file_path, week_dates)
    if existing:
        crawler.merge_floor10_data(meal_data, existing)
        count = sum(len(v) for v in existing.values())
        print(f"✓ 기존 10층 식단 {count}개 항목 보존 (10층은 이미지 커밋으로만 갱신)")
    else:
        print("   기존 10층 데이터 없음 → placeholder 유지 (이미지 커밋 시 채워짐)")


def send_daily_lunch(date: str = None, db_path: str = "db", dry_run: bool = False) -> bool:
    """
    해당 날짜의 점심 식단 전송
    
    Args:
        date: 날짜 (YYYY-MM-DD), None이면 오늘
        db_path: Markdown 파일 저장 경로
        dry_run: True이면 웹훅 전송 없이 결과만 출력
    
    Returns:
        성공 여부
    """
    kst = timezone(timedelta(hours=9))
    
    if date is None:
        now_kst = datetime.now(kst)
        date = now_kst.strftime('%Y-%m-%d')
    else:
        now_kst = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=kst)
    
    # 주말 체크 (월~금만 식단 전송)
    if now_kst.weekday() >= 5:
        print("=" * 60)
        print(f"ℹ️  주말에는 점심 식단을 전송하지 않습니다: {date} ({WEEKDAY_NAMES_KR[now_kst.weekday()]}요일)")
        print("=" * 60)
        return True
    
    print("=" * 60)
    if dry_run:
        print(f"🔍 일일 점심 식단 확인 (테스트 모드): {date}")
    else:
        print(f"🍽️ 일일 점심 식단 전송: {date}")
    print("=" * 60)
    
    try:
        sender = NotificationSender(skip_validation=dry_run)
        success = sender.load_and_send_daily(date, db_path, dry_run)
        
        if not dry_run:
            if success:
                print("✓ 일일 식단 전송 완료")
            else:
                print("✗ 일일 식단 전송 실패")
        
        return success
    
    except Exception as e:
        print(f"✗ 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """CLI 진입점"""
    load_dotenv()
    
    parser = argparse.ArgumentParser(
        description='SSAFY 식단 알림 봇',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 웰스토리 API로 이번 주 식단 크롤링 (10층은 Mattermost 자동수집)
  python main.py crawl

  # 커밋한 이미지로 10층 식단 파싱해 병합
  python main.py crawl --floor10-image ../images/10f.png

  # 오늘 점심 식단 전송
  python main.py daily

  # 특정 날짜 점심 식단 전송
  python main.py daily --date 2026-01-15
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='실행할 명령')
    
    # crawl 명령
    crawl_parser = subparsers.add_parser('crawl', help='웰스토리 API로 이번 주 식단 크롤링')
    crawl_parser.add_argument('--db', default='db', help='Markdown 파일 저장 경로 (기본값: db)')
    crawl_parser.add_argument(
        '--floor10-image',
        help='10층 식단 이미지 경로. 지정 시 Mattermost 자동수집 대신 이 이미지를 파싱해 병합',
    )
    
    # daily 명령
    daily_parser = subparsers.add_parser('daily', help='일일 점심 식단 전송')
    daily_parser.add_argument('--date', help='날짜 (YYYY-MM-DD), 미지정 시 오늘')
    daily_parser.add_argument('--db', default='db', help='Markdown 파일 저장 경로 (기본값: db)')
    daily_parser.add_argument('--dry-run', action='store_true', help='웹훅 전송 없이 결과만 확인')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    try:
        if args.command == 'crawl':
            success = crawl_weekly(args.db, getattr(args, 'floor10_image', None))
            return 0 if success else 1
        
        elif args.command == 'daily':
            dry_run = getattr(args, 'dry_run', False)
            success = send_daily_lunch(args.date, args.db, dry_run)
            return 0 if success else 1
    
    except Exception as e:
        print(f"\n✗ 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
