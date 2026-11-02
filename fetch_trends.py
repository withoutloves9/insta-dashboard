import os
import re
import io
import time
import json
from datetime import datetime, timedelta
import pandas as pd
import requests

SHEET_ID = "1XeY1nBDqFVFoUYQiYD59liuD3Gij4TixJNKo3P_mwxw"
SHEET_GID = "776223726"

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
    return unique_artists

def fetch_single_artist_daily(artist_name, start_date, end_date, client_id, client_secret):
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
            "timeUnit": "date",  # 일간(Daily) 단위로 조회하여 네이버 웹 조회와 수치 일치화
            "keywordGroups": [{"groupName": artist_name, "keywords": [artist_name]}]
        }
        if g_val: body["gender"] = g_val
            
        try:
            res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
            if res.status_code == 200:
                data = res.json().get("results", [])
                if data and len(data) > 0:
                    period_data = data[0].get("data", [])
                    if not dates and g_key == "all":
                        dates = [d["period"] for d in period_data]
                    result_series[g_key] = [d["ratio"] for d in period_data]
            time.sleep(0.15)
        except Exception as e:
            pass
            
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
    
    for idx, a in enumerate(artists):
        name = a["name"]
        dates, raw_res = fetch_single_artist_daily(name, start_str, end_str, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)
        
        if not global_dates and dates: global_dates = dates
            
        all_series = raw_res.get("all", [])
        total_1year = sum(all_series)
        total_6months = sum(all_series[-182:]) if len(all_series) >= 182 else sum(all_series)
        total_3months = sum(all_series[-91:]) if len(all_series) >= 91 else sum(all_series)

        output_artists.append({
            "agency": a["agency"],
            "name": name,
            "naver": { "all": all_series, "m": raw_res.get("m", []), "f": raw_res.get("f", []) },
            "summary": {
                "total_1year": round(total_1year, 2),
                "total_6months": round(total_6months, 2),
                "total_3months": round(total_3months, 2)
            },
            "google": { "all": [0]*len(all_series) }
        })

    output_data = {
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "anchor": "일간 절대 수치 조회",
        "dates": global_dates,
        "artists": output_artists
    }
    
    with open("trends_data.js", "w", encoding="utf-8") as f:
        f.write(f"window.TRENDS_DATA = {json.dumps(output_data, ensure_ascii=False)};")
    print("🎉 일간 절대 데이터 생성 완료!")
