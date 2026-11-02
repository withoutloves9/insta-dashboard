import os
import re
import io
import time
import json
from datetime import datetime, timedelta
import pandas as pd
import requests

# ==========================================
# [설정 영역] 구글 시트 정보
# ==========================================
SHEET_ID = "1XeY1nBDqFVFoUYQiYD59liuD3Gij4TixJNKo3P_mwxw"
SHEET_GID = "776223726"
# ==========================================

def extract_artists_from_sheet(sheet_id, gid):
    if "spreadsheets/d/" in sheet_id:
        sheet_id = sheet_id.split("spreadsheets/d/")[1].split("/")[0]
    sheet_id = sheet_id.strip()
    gid = re.sub(r'[^0-9]', '', str(gid)) or "0"

    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}"
    res = requests.get(url, timeout=15)
    res.encoding = 'utf-8'
    df = pd.read_csv(io.StringIO(res.text), header=None, dtype=str).fillna("")

    artists = []
    skip_words = ['소속배우수', '계약종료', '전체', 'MGMT', '배우수', 'update', '소속사', '아티스트', '이름']

    for r in range(df.shape[0]):
        row_vals = [str(df.iloc[r, c]).strip() for c in range(df.shape[1])]
        c = 0
        while c < len(row_vals) - 1:
            agency = row_vals[c]
            name = row_vals[c + 1]
            if agency and name:
                if any(w.lower() in agency.lower() or w.lower() in name.lower() for w in skip_words):
                    c += 1
                    continue
                if re.match(r'^[\d\.\-\/\s]+$', agency) or re.match(r'^[\d\.\-\/\s]+$', name):
                    c += 1
                    continue
                if len(agency) <= 15 and len(name) <= 15:
                    artists.append({"agency": agency, "name": name})
                    c += 2
                    continue
            c += 1

    unique_artists = []
    seen = set()
    for a in artists:
        if a["name"] not in seen:
            seen.add(a["name"])
            unique_artists.append(a)

    print(f"✅ 구글 시트에서 총 {len(unique_artists)}명의 아티스트 명단 추출 완료")
    return unique_artists

def fetch_single_artist_naver_datalab(artist_name, start_date, end_date, client_id, client_secret):
    url = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
        "Content-Type": "application/json"
    }
    
    genders = {"all": "", "m": "m", "f": "f"}
    result_series = {"all": [], "m": [], "f": []}
    dates = []

    for g_key, g_val in genders.items():
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "timeUnit": "week",
            "keywordGroups": [
                {
                    "groupName": artist_name,
                    "keywords": [artist_name]
                }
            ]
        }
        if g_val:
            body["gender"] = g_val
            
        try:
            res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
            if res.status_code == 200:
                data = res.json().get("results", [])
                if data and len(data) > 0:
                    period_data = data[0].get("data", [])
                    if not dates and g_key == "all":
                        dates = [d["period"] for d in period_data]
                    result_series[g_key] = [d["ratio"] for d in period_data]
            time.sleep(0.15) # API 호출 제한 방지
        except Exception as e:
            print(f"⚠️ {artist_name} [{g_key}] 수신 중 오류: {e}")
            
    return dates, result_series

if __name__ == "__main__":
    NAVER_CLIENT_ID = (os.environ.get("NAVER_CLIENT_ID") or "").strip().strip('"').strip("'")
    NAVER_CLIENT_SECRET = (os.environ.get("NAVER_CLIENT_SECRET") or "").strip().strip('"').strip("'")

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=365)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    
    artists = extract_artists_from_sheet(SHEET_ID, SHEET_GID)
    
    global_dates = []
    output_artists = []
    
    print(f"⏳ 총 {len(artists)}명의 아티스트 개별 절대 데이터 수집 시작...")
    
    for idx, a in enumerate(artists):
        name = a["name"]
        dates, raw_res = fetch_single_artist_naver_datalab(name, start_str, end_str, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)
        
        if not global_dates and dates:
            global_dates = dates
            
        all_series = raw_res.get("all", [])
        m_series = raw_res.get("m", [])
        f_series = raw_res.get("f", [])
        
        # 개별 절대 수치 기반 기간별 누적 합산(Sum) 계산
        total_1year = sum(all_series)
        total_6months = sum(all_series[-26:]) if len(all_series) >= 26 else sum(all_series)
        total_3months = sum(all_series[-13:]) if len(all_series) >= 13 else sum(all_series)

        output_artists.append({
            "agency": a["agency"],
            "name": name,
            "naver": {
                "all": all_series,
                "m": m_series,
                "f": f_series
            },
            "summary": {
                "total_1year": round(total_1year, 2),
                "total_6months": round(total_6months, 2),
                "total_3months": round(total_3months, 2)
            },
            "google": {
                "all": [0]*len(all_series)
            }
        })
        
        if (idx + 1) % 10 == 0:
            print(f"   진행 상황: {idx + 1}/{len(artists)}명 완료...")

    output_data = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "anchor": "개별 절대 수치 조회",
        "dates": global_dates if global_dates else [],
        "artists": output_artists
    }
    
    js_content = f"window.TRENDS_DATA = {json.dumps(output_data, ensure_ascii=False)};"
    with open("trends_data.js", "w", encoding="utf-8") as f:
        f.write(js_content)
        
    print("🎉 개별 절대 수치 및 누적 합산 데이터 생성 완료!")
