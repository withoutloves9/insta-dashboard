import os
import time
import datetime
import json
import requests

# ==============================================================================
# [설정] Apify 토큰 및 구글 시트 웹 앱 URL
# ==============================================================================
APIFY_TOKEN = "apify_api_y0sf9F3QjeZaUJH2vGDHk81T5neavY1eZLQE"
WEB_APP_URL = "https://script.google.com/macros/s/AKfycbzwPCkEd8bPn3a_6kZssRzqDqMoVRVuIf_FIrZPZkzrHpzl7O-HKuwQTWVAAP7I0JgK/exec"


def run_full_collection():
    print("=" * 65)
    print("🚀 인스타그램 월별 지표 완전 자동 수집기 시작")
    print("=" * 65)

    # 1. 구글 시트에서 전체 아티스트 명단(Artists 탭) 불러오기
    print("⏳ 구글 시트에서 아티스트 명단을 불러오는 중...")
    try:
        res = requests.get(WEB_APP_URL, timeout=20)
        if "<html" in res.text.lower():
            print("❌ [오류] 웹 앱 URL 권한이 '모든 사용자'로 설정되지 않았거나 잘못되었습니다!")
            return

        sheet_data = res.json()
        artists = sheet_data.get("artists", [])
        existing_records = sheet_data.get("records", {})
        print(f"✅ 명단 로드 완료! (총 {len(artists)}명)\n")
    except Exception as e:
        print(f"❌ 구글 시트 명단 불러오기 실패: {e}")
        return

    if not artists:
        print("⚠️ 불러온 아티스트 명단이 비어 있습니다. 구글 시트를 확인해 주세요.")
        return

    usernames = []
    artist_map = {}
    for artist in artists:
        handle = artist.get("handle", "-")
        if handle and handle != '-':
            clean_handle = handle.replace('@', '').strip().lower()
            usernames.append(clean_handle)
            artist_map[clean_handle] = artist

    print(f"⏳ Apify에 총 {len(usernames)}개 계정 데이터 수집 요청 중...")
    
    # 2. Apify 비동기 작업 시작 (201 에러 우회 방식)
    start_url = f"https://api.apify.com/v2/acts/apify~instagram-profile-scraper/runs?token={APIFY_TOKEN}"
    payload = {
        "usernames": usernames,
        "resultsLimit": 10
    }

    try:
        start_res = requests.post(start_url, json=payload, timeout=30)
        if start_res.status_code not in [200, 201]:
            print(f"❌ Apify 작업 시작 실패 (HTTP {start_res.status_code})")
            return
        
        run_data = start_res.json().get("data", {})
        run_id = run_data.get("id")
        dataset_id = run_data.get("defaultDatasetId")
        print(f"⏳ Apify 서버에서 데이터 수집 중... (약 20~40초 소요)")
    except Exception as e:
        print(f"❌ 작업 요청 중 통신 오류: {e}")
        return

    # 3. 작업 완료 대기 (Polling)
    success_triggered = False
    for _ in range(30):
        time.sleep(5)
        try:
            status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
            status_res = requests.get(status_url, timeout=15)
            status_data = status_res.json().get("data", {})
            status = status_data.get("status")
            
            if status == "SUCCEEDED":
                success_triggered = True
                break
            elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                print(f"❌ Apify 작업 실패 상태: {status}")
                return
        except:
            pass

    if not success_triggered:
        print("❌ Apify 작업 시간이 초과되었습니다.")
        return

    # 4. 데이터셋 가져오기
    print(f"✅ 데이터 추출 중...\n")
    dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}"
    
    try:
        data_res = requests.get(dataset_url, timeout=30)
        if data_res.status_code != 200:
            print(f"❌ 데이터 가져오기 실패 (HTTP {data_res.status_code})")
            return
        
        profiles = data_res.json()
    except Exception as e:
        print(f"❌ 데이터 추출 오류: {e}")
        return

    now = datetime.datetime.now()
    year_str = str(now.year)
    month_str = str(now.month)
    year_month = now.strftime('%Y-%m')
    timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

    records_db = existing_records
    print("-" * 65)
    print("📊 [결과 요약 & 구글 시트 실시간 전송]")
    print("-" * 65)

    success_count = 0
    for profile in profiles:
        username = (profile.get("username") or "").lower()
        artist_info = artist_map.get(username)
        if not artist_info:
            continue

        artist_id = artist_info.get("id")
        agency = artist_info.get("agency")
        name = artist_info.get("name")
        handle = artist_info.get("handle")

        followers = int(profile.get("followersCount", 0) or 0)
        latest_posts = (profile.get("latestPosts") or [])[:10]

        likes_10 = 0
        comments_10 = 0

        for post in latest_posts:
            l_cnt = post.get("likesCount")
            if l_cnt is None and "childPosts" in post:
                for cp in post["childPosts"]:
                    if cp.get("likesCount") is not None:
                        l_cnt = cp.get("likesCount")
                        break
            if l_cnt is not None:
                try: likes_10 += int(l_cnt)
                except: pass

            c_cnt = post.get("commentsCount")
            if c_cnt is None and "childPosts" in post:
                for cp in post["childPosts"]:
                    if cp.get("commentsCount") is not None:
                        c_cnt = cp.get("commentsCount")
                        break
            if c_cnt is not None:
                try: comments_10 += int(c_cnt)
                except: pass

        success_count += 1

        if artist_id not in records_db:
            records_db[artist_id] = {}
        if year_str not in records_db[artist_id]:
            records_db[artist_id][year_str] = {}
            
        records_db[artist_id][year_str][month_str] = {
            "followers": followers,
            "likes10": likes_10,
            "comments10": comments_10,
            "updatedAt": timestamp[:10]
        }

        # 구글 시트로 전송 (타임아웃 45초로 연장 및 빈 응답 방어 적용)
        sheet_payload = {
            "yearMonth": year_month,
            "agency": agency,
            "name": name,
            "handle": handle,
            "followers": followers,
            "likes10": likes_10,
            "comments10": comments_10,
            "timestamp": timestamp
        }
        
        sheet_status = "❌ 전송 실패"
        try:
            post_res = requests.post(WEB_APP_URL, json=sheet_payload, timeout=45)
            if post_res.text and post_res.text.strip():
                res_json = post_res.json()
                if res_json.get("status") == "success":
                    sheet_status = "✅ 구글 시트 전송 성공"
                else:
                    sheet_status = f"⚠️ 응답 이상: {res_json}"
            else:
                sheet_status = "⚠️ 구글 시트 빈 응답"
        except Exception as e:
            sheet_status = f"❌ 통신 에러"

        print(f"[{agency}] {name} (@{handle})")
        print(f" └ 팔로워: {followers:,}명 | 좋아요: {likes_10:,} | 댓글: {comments_10:,} | {sheet_status}\n")

    # 백업 JSON 파일 생성
    current_dir = os.path.dirname(os.path.abspath(__file__))
    backup_json_path = os.path.join(current_dir, "insta_dashboard_backup.json")
    final_dashboard_data = {
        "artists": artists,
        "records": records_db
    }
    
    with open(backup_json_path, "w", encoding="utf-8") as f:
        json.dump(final_dashboard_data, f, ensure_ascii=False, indent=2)

    print("=" * 65)
    print(f"🎉 전체 작업 완료! ({success_count}명 처리됨)")
    print(f"📂 백업 JSON 저장 위치: {backup_json_path}")
    print("=" * 65)


if __name__ == "__main__":
    run_full_collection()