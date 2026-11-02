import os
import re
import io
import time
import json
from datetime import datetime, timedelta
import pandas as pd
import requests

# ==========================================
# [설정 영역]
# ==========================================
SHEET_ID = "1XeY1nBDqFVFoUYQiYD59liuD3Gij4TixJNKo3P_mwxw"
SHEET_GID = "776223726"
ANCHOR_NAME = "공유"
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

    return unique_artists

def generate_default_weekly_dates(end_dt, weeks=52):
    dates = []
    for i in range(weeks - 1, -1, -1):
        d = end_dt - timedelta(days=i * 7)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates

def fetch_naver_datalab(artists, anchor_name, start_date, end_date, client_id, client_secret):
    url = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
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
                
            time.sleep(0.2)

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
        all_series = naver_all.get(name, [0]*len(dates))
        
        # 1년, 6개월, 3개월 누적 합산(Sum) 계산
        total_1year = sum(all_series)
        total_6months = sum(all_series[-26:]) if len(all_series) >= 26 else sum(all_series)
        total_3months = sum(all_series[-13:]) if len(all_series) >= 13 else sum(all_series)

        output_data["artists"].append({
            "agency": a["agency"],
            "name": name,
            "naver": {
                "all": all_series,
                "m": naver_m.get(name, [0]*len(dates)),
                "f": naver_f.get(name, [0]*len(dates))
            },
            "summary": {
                "total_1year": round(total_1year, 2),
                "total_6months": round(total_6months, 2),
                "total_3months": round(total_3months, 2)
            },
            "google": {
                "all": [0]*len(dates)
            }
        })
        
    js_content = f"window.TRENDS_DATA = {json.dumps(output_data, ensure_ascii=False)};"
    with open("trends_data.js", "w", encoding="utf-8") as f:
        f.write(js_content)
        
    print("🎉 누적 합산 데이터 포함된 trends_data.js 생성 완료!")
