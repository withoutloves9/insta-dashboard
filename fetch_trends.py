import os
import re
import io
import time
import json
from datetime import datetime, timedelta
import pandas as pd
import requests
from pytrends.request import TrendReq

# ==========================================
# [설정 영역] 아래 2줄에 본인 구글 시트 정보를 꼭 입력하세요!
# ==========================================
SHEET_ID = "1XeY1nBDqFVFoUYQiYD59liuD3Gij4TixJNKo3P_mwxw"
SHEET_GID = "776223726"  # 시트 주소 맨 뒤 gid= 뒤에 있는 숫자 (첫 번째 탭이면 보통 0)

ANCHOR_NAME = "공유" # 기준 배우
# ==========================================

def extract_artists_from_sheet(sheet_id, gid):
    # 혹시라도 전체 URL이나 불필요한 기호가 들어갔을 경우 자동으로 순수 ID만 추출
    if "spreadsheets/d/" in sheet_id:
        sheet_id = sheet_id.split("spreadsheets/d/")[1].split("/")[0]
    sheet_id = sheet_id.strip()
    gid = re.sub(r'[^0-9]', '', str(gid)) or "0"

    # 호환성이 가장 높은 구글 gviz CSV 엔드포인트와 기본 export 엔드포인트 순차 시도
    urls = [
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}",
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    ]

    df = None
    for url in urls:
        try:
            res = requests.get(url, timeout=15)
            res.encoding = 'utf-8'
            if res.status_code == 200 and "<!DOCTYPE html>" not in res.text[:200]:
                df = pd.read_csv(io.StringIO(res.text), header=None, dtype=str).fillna("")
                break
            else:
                print(f"⚠️ 시트 응답 확인 필요 (상태코드 {res.status_code}): 공개 설정 또는 ID 확인 필요")
        except Exception as e:
            print(f"⚠️ URL 접속 실패: {e}")

    if df is None or df.empty:
        raise ValueError("❌ 구글 시트 데이터를 읽어오지 못했습니다. SHEET_ID와 공유 권한(링크가 있는 모든 사용자)을 확인하세요.")

    print(f"📊 시트 로드 성공 (크기: {df.shape[0]}행 x {df.shape[1]}열)")

    artists = []
    skip_words = ['소속배우수', '계약종료', '전체', 'MGMT', '배우수', 'update', '소속사', '아티스트', '이름']

    # 모든 행과 열을 순회하며 (소속사, 배우명) 쌍 추출
    for r in range(df.shape[0]):
        row_vals = [str(df.iloc[r, c]).strip() for c in range(df.shape[1])]
        c = 0
        while c < len(row_vals) - 1:
            agency = row_vals[c]
            name = row_vals[c + 1]

            if agency and name:
                # 제외 키워드 포함 여부 확인
                if any(w.lower() in agency.lower() or w.lower() in name.lower() for w in skip_words):
                    c += 1
                    continue
                # 숫자만 있거나 날짜 형식(2025-06, 26.10.29 등)인 경우 제외[cite: 1]
                if re.match(r'^[\d\.\-\/\s]+$', agency) or re.match(r'^[\d\.\-\/\s]+$', name):
                    c += 1
                    continue
                # 정상적인 (소속사, 배우명) 쌍으로 판단
                if len(agency) <= 15 and len(name) <= 15:
                    artists.append({"agency": agency, "name": name})
                    c += 2  # 찾았으면 다음 세트로 점프
                    continue
            c += 1

    # 중복 제거
    unique_artists = []
    seen = set()
    for a in artists:
        if a["name"] not in seen:
            seen.add(a["name"])
            unique_artists.append(a)

    print(f"✅ 구글 시트에서 총 {len(unique_artists)}명의 현직 배우 명단을 추출했습니다.")
    if len(unique_artists) == 0:
        print("🔍 [디버그용] 시트 상위 10줄 내용 출력:")
        print(df.head(10).to_string())
        raise ValueError("❌ 추출된 배우가 0명입니다. 위 디버그 표를 확인해 주세요.")

    return unique_artists

def fetch_naver_datalab(artists, anchor_name, start_date, end_date, client_id, client_secret):
    url = "https://openapi.naver.com/v1/datalab/search"
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
        "Content-Type": "application/json"
    }
    
    others = [a["name"] for a in artists if a["name"] != anchor_name]
    chunks = [others[i:i + 4] for i in range(0, len(others), 4)]
    
    genders = {"all": "", "m": "m", "f": "f"}
    raw_scores = {"all": {}, "m": {}, "f": {}}
    dates = []

    for g_key, g_val in genders.items():
        print(f"⏳ 네이버 데이터랩 [{g_key.upper()}] 수집 중... (총 {len(chunks)}회 호출)")
        base_anchor_avg = None
        
        for idx, chunk in enumerate(chunks):
            group_names = [anchor_name] + chunk
            keyword_groups = [{"groupName": name, "keywords": [name]} for name in group_names]
            
            body = {
                "startDate": start_date,
                "endDate": end_date,
                "timeUnit": "week",
                "keywordGroups": keyword_groups
            }
            if g_val:
                body["gender"] = g_val
                
            res = requests.post(url, headers=headers, data=json.dumps(body))
            if res.status_code != 200:
                print(f"❌ 네이버 API 오류: {res.text}")
                continue
                
            results = res.json().get("results", [])
            if not dates and results:
                dates = [d["period"] for d in results[0]["data"]]
                
            chunk_map = {}
            for r in results:
                period_dict = {d["period"]: d["ratio"] for d in r["data"]}
                chunk_map[r["title"]] = [period_dict.get(dt, 0) for dt in dates]
                
            anchor_series = chunk_map.get(anchor_name, [1]*len(dates))
            current_anchor_avg = sum(anchor_series) / len(anchor_series) if sum(anchor_series) > 0 else 1
            
            if idx == 0:
                base_anchor_avg = current_anchor_avg
                raw_scores[g_key][anchor_name] = anchor_series
                scale_factor = 1.0
            else:
                scale_factor = base_anchor_avg / current_anchor_avg
                
            for name in chunk:
                series = chunk_map.get(name, [0]*len(dates))
                raw_scores[g_key][name] = [round(val * scale_factor, 4) for val in series]
                
            time.sleep(0.15)

    return dates, raw_scores

def fetch_google_trends(artists, anchor_name, dates_len):
    pytrends = TrendReq(hl='ko-KR', tz=540)
    others = [a["name"] for a in artists if a["name"] != anchor_name]
    chunks = [others[i:i + 4] for i in range(0, len(others), 4)]
    
    google_scores = {}
    base_anchor_avg = None
    
    print(f"⏳ 구글 트렌드 수집 중... (총 {len(chunks)}회 호출)")
    for idx, chunk in enumerate(chunks):
        kw_list = [anchor_name] + chunk
        try:
            pytrends.build_payload(kw_list, cat=0, timeframe='today 12-m', geo='KR', gprop='')
            df = pytrends.interest_over_time()
            
            if df.empty:
                for name in kw_list:
                    if name not in google_scores:
                        google_scores[name] = [0] * dates_len
                continue
                
            anchor_series = df[anchor_name].tolist()[-dates_len:]
            current_anchor_avg = sum(anchor_series) / len(anchor_series) if sum(anchor_series) > 0 else 1
            
            if idx == 0:
                base_anchor_avg = current_anchor_avg
                google_scores[anchor_name] = anchor_series
                scale_factor = 1.0
            else:
                scale_factor = base_anchor_avg / current_anchor_avg
                
            for name in chunk:
                series = df[name].tolist()[-dates_len:] if name in df else [0] * dates_len
                google_scores[name] = [round(val * scale_factor, 4) for val in series]
                
            print(f"  - 구글 트렌드 ({idx+1}/{len(chunks)}) 완료")
            time.sleep(6)
        except Exception as e:
            print(f"⚠️ 구글 트렌드 ({idx+1}번째 묶음) 일시 오류: {e}")
            for name in chunk:
                google_scores[name] = [0] * dates_len
            time.sleep(10)
            
    return google_scores

def normalize_to_100(score_dict):
    max_val = 0
    for series in score_dict.values():
        if series:
            m = max(series)
            if m > max_val:
                max_val = m
    if max_val == 0:
        return score_dict
    return {
        name: [round((v / max_val) * 100, 2) for v in series]
        for name, series in score_dict.items()
    }

if __name__ == "__main__":
    NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
    NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=365)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    
    artists = extract_artists_from_sheet(SHEET_ID, SHEET_GID)
    if not any(a["name"] == ANCHOR_NAME for a in artists):
        ANCHOR_NAME = artists[0]["name"]
        
    dates, naver_raw = fetch_naver_datalab(artists, ANCHOR_NAME, start_str, end_str, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)
    naver_all = normalize_to_100(naver_raw["all"])
    naver_m = normalize_to_100(naver_raw["m"])
    naver_f = normalize_to_100(naver_raw["f"])
    
    google_raw = fetch_google_trends(artists, ANCHOR_NAME, len(dates))
    google_all = normalize_to_100(google_raw)
    
    output_data = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "anchor": ANCHOR_NAME,
        "dates": dates,
        "artists": []
    }
    
    for a in artists:
        name = a["name"]
        output_data["artists"].append({
            "agency": a["agency"],
            "name": name,
            "naver": {
                "all": naver_all.get(name, [0]*len(dates)),
                "m": naver_m.get(name, [0]*len(dates)),
                "f": naver_f.get(name, [0]*len(dates))
            },
            "google": {
                "all": google_all.get(name, [0]*len(dates))
            }
        })
        
    js_content = f"window.TRENDS_DATA = {json.dumps(output_data, ensure_ascii=False)};"
    with open("trends_data.js", "w", encoding="utf-8") as f:
        f.write(js_content)
        
    print("🎉 trends_data.js 생성 완료!")
