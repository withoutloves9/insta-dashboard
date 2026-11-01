import os
import re
import io
import time
import json
from datetime import datetime, timedelta
import pandas as pd
import requests

SHEET_ID = "1XeY1nBDqFVFoUYQiYD59liuD3Gij4TixJNKo3P_mwxw"
SHEET_GID = "776223726"  # 시트 주소 맨 뒤 gid= 뒤에 있는 숫자 (첫 번째 탭이면 보통 0)

ANCHOR_NAME = "공유" # 기준 배우

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

    print(f"✅ 구글 시트에서 총 {len(unique_artists)}명의 현직 배우 명단 추출 완료")
    return unique_artists

def generate_default_weekly_dates(end_dt, weeks=52):
    dates = []
    for i in range(weeks - 1, -1, -1):
        d = end_dt - timedelta(days=i * 7)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates

def fetch_naver_datalab(artists, anchor_name, start_date, end_date, client_id, client_secret):
    # NCP API HUB 데이터랩 엔드포인트
    url = "https://apigw.apigw.ntruss.com/naver-api-hub/v1/datalab/search"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
        "Content-Type": "application/json"
    }
    
    others = [a["name"] for a in artists if a["name"] != anchor_name]
    chunks = [others[i:i + 4] for i in range(0, len(others), 4)]
    
    genders = {"all": "", "m": "m", "f": "f"}
    raw_scores = {"all": {}, "m": {}, "f": {}}
    dates = []

    for g_key, g_val in genders.items():
        print(f"⏳ NCP API HUB 데이터랩 [{g_key.upper()}] 수집 중... (총 {len(chunks)}회 호출)")
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
                print(f"❌ NCP API HUB 오류 (코드 {res.status_code})")
                print(f"🔍 NCP API HUB 상세 에러 내용: {res.text}")
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
    NAVER_CLIENT_ID = (os.environ.get("NAVER_CLIENT_ID") or "").strip().strip('"').strip("'")
    NAVER_CLIENT_SECRET = (os.environ.get("NAVER_CLIENT_SECRET") or "").strip().strip('"').strip("'")

    print(f"🔑 [키 점검] ID 길이: {len(NAVER_CLIENT_ID)} | SECRET 길이: {len(NAVER_CLIENT_SECRET)}")

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=365)
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    
    artists = extract_artists_from_sheet(SHEET_ID, SHEET_GID)
    if not any(a["name"] == ANCHOR_NAME for a in artists):
        ANCHOR_NAME = artists[0]["name"]
        
    dates, naver_raw = fetch_naver_datalab(artists, ANCHOR_NAME, start_str, end_str, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET)
    
    if not dates:
        dates = generate_default_weekly_dates(end_dt, 52)

    naver_all = normalize_to_100(naver_raw.get("all", {}))
    naver_m = normalize_to_100(naver_raw.get("m", {}))
    naver_f = normalize_to_100(naver_raw.get("f", {}))
    
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
                "all": [0]*len(dates)
            }
        })
        
    js_content = f"window.TRENDS_DATA = {json.dumps(output_data, ensure_ascii=False)};"
    with open("trends_data.js", "w", encoding="utf-8") as f:
        f.write(js_content)
        
    print("🎉 trends_data.js 생성 완료!")
