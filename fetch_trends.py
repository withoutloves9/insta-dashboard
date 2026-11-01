import os
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

# 129명의 상대 비율을 하나로 맞춰줄 기준 배우[cite: 1]
ANCHOR_NAME = "공유"
# ==========================================

def extract_artists_from_sheet(sheet_id, gid):
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    df = pd.read_csv(csv_url, header=None)
    
    artists = []
    skip_keywords = {'소속배우수', '계약종료', '전체 MGMT 배우수', 'nan', ''}
    
    for col in range(df.shape[1] - 1):
        for row in range(df.shape[0]):
            agency = str(df.iloc[row, col]).strip()
            name = str(df.iloc[row, col + 1]).strip()
            
            if not agency or not name or agency == 'nan' or name == 'nan':
                continue
            if any(k in agency for k in skip_keywords) or 'update' in agency.lower():
                continue
            if name.replace('.', '').isdigit() or agency.replace('.', '').isdigit():
                continue
            if len(name) == 7 and '-' in name:
                continue
                
            artists.append({"agency": agency, "name": name})
            
    unique_artists = []
    seen = set()
    for a in artists:
        if a["name"] not in seen:
            seen.add(a["name"])
            unique_artists.append(a)
            
    print(f"✅ 구글 시트에서 총 {len(unique_artists)}명의 현직 배우 명단을 추출했습니다.")
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
