import os
import re
import io
import time
import json
from datetime import datetime, timedelta
import pandas as pd
import requests

# ==========================================
# [설정 영역] 아래 2줄에 본인 구글 시트 정보를 꼭 입력하세요!
# ==========================================
SHEET_ID = "1XeY1nBDqFVFoUYQiYD59liuD3Gij4TixJNKo3P_mwxw"
SHEET_GID = "776223726"  # 시트 주소 맨 뒤 gid= 뒤에 있는 숫자 (첫 번째 탭이면 보통 0)

ANCHOR_NAME = "공유" # 기준 배우
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

    print(f"✅ 구글 시트에서 총 {len(unique_artists)}명의 현직 배우 명단 추출 완료")
    return unique_artists

def generate_default_weekly_dates(end_dt, weeks=52):
    dates = []
    for i in range(weeks - 1, -1, -1):
        d = end_dt - timedelta(days=i * 7)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates

def fetch_naver_datalab(artists, anchor_name, start_date, end_date, client_id, client_secret):
    # 공식 가이드 기준 NCP API HUB 검색어 트렌드 정식 엔드포인트
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
                print(f"❌ API HUB 오류 (코드 {res.status_code}): {res.text}")
                continue
                
            results = res.json().get("results", [])
            if not dates and results:
                dates = [d["period"] for d in results[0]["data"]]
                
            chunk_
