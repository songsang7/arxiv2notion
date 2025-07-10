import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from google import genai
import time
from google.genai import types
import httpx
import re

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("DATABASE_ID")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# ✅ Hard-code non-secret configurations directly in the script
KEYWORDS = [
    "reinforcement learning",
    "homography estimation",
    "self supervised learning",
    "unsupervised learning",
  ]
ALLOWED_SUBJECTS = {"cs.CV", "cs.AI", "cs.LG", "cs.CG"}
MY_RESEARCH_AREA = "내 연구는 강화학습이나 비지도 학습 또는 자기지도학습 처럼 ground truth를 크게 요구하지 않는 연구가 중심이야. 응용분야는 주로 컴퓨터 비전분야에 응용하는 것을 선호하지만, 꼭 비전이 아니더라도 로봇제어와 같은 분야로도 응용하는 것에 관심이 있어."
LOOKBACK_DAYS = 5

# Basic check to ensure secrets were loaded
if not all([NOTION_TOKEN, DATABASE_ID, GOOGLE_API_KEY]):
    raise ValueError("❌ One or more secret environment variables are not set. Please check your GitHub repository secrets.")

MODEL_LIST = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite-preview-06-17"]

current_model_index = 0 # 사용할 모델을 가리키는 인덱스

# ✅ 날짜 계산도 config 기반으로
today = datetime.today()
yesterday = today - timedelta(days=LOOKBACK_DAYS)

# ✅ Gemini client 설정
client = genai.Client(api_key=GOOGLE_API_KEY)

def fetch_existing_titles():
    """Notion 데이터베이스에서 기존 논문 제목들을 가져옵니다."""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    titles = set()
    has_more = True
    next_cursor = None
    while has_more:
        data = {"start_cursor": next_cursor} if next_cursor else {}
        try:
            res = requests.post(url, headers=headers, json=data, timeout=10)
            res.raise_for_status()
            results = res.json()
            for page in results["results"]:
                try:
                    # ✨ 공백 정규화 추가
                    title = ' '.join(page["properties"]["Paper"]["title"][0]["text"]["content"].split())
                    titles.add(title)
                except (KeyError, IndexError):
                    continue
            has_more = results.get("has_more", False)
            next_cursor = results.get("next_cursor")
        except requests.exceptions.RequestException as e:
            print(f"❌ Notion 제목 조회 중 오류 발생: {e}")
            break
    return titles

def fetch_arxiv_papers():
    """키워드를 기반으로 arXiv에서 논문을 검색하고 날짜와 카테고리로 필터링합니다."""
    base_url = "http://export.arxiv.org/api/query?"
    unique_papers = {}
    print("⬇️  키워드 기반 arXiv 논문 다운로드 시작...")
    for keyword in set(KEYWORDS):
        print(f"🔎 키워드 검색 중: \"{keyword}\"")
        search_query = f'ti:"{keyword}" OR abs:"{keyword}"'
        params = f"search_query={search_query}&sortBy=submittedDate&sortOrder=descending&max_results=50"
        try:
            response = requests.get(base_url + params, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"❌ \"{keyword}\" 검색 중 arXiv API 오류: {e}")
            continue
        soup = BeautifulSoup(response.content, 'xml')
        entries = soup.find_all('entry')
        for entry in entries:
            # ArXiv ID (e.g., http://arxiv.org/abs/2401.12345)
            paper_abs_url = entry.id.text.strip()
            # PDF URL (e.g., http://arxiv.org/pdf/2401.12345.pdf)
            paper_pdf_url = paper_abs_url.replace('abs', 'pdf')

            if paper_abs_url not in unique_papers:
                # ✨ 제목과 초록의 연속 공백 및 줄바꿈을 하나의 공백으로 변경
                clean_title = ' '.join(entry.title.text.strip().split())
                clean_abstract = ' '.join(entry.summary.text.strip().split())

                unique_papers[paper_abs_url] = {
                    'title': clean_title,
                    'link': paper_abs_url, # Abstract page URL
                    'pdf_link': paper_pdf_url, # PDF URL
                    'updated_str': entry.updated.text,
                    'abstract': clean_abstract, # ✨ 원본 초록 (요약 전)
                    'author': entry.author.find('name').text.strip() if entry.author else 'arXiv',
                    'categories': [cat['term'] for cat in entry.find_all('category')]
                }
        time.sleep(1)
    print(f"👍 총 {len(unique_papers)}개의 고유 논문 발견. 필터링 시작...")
    filtered_papers = []
    for paper in unique_papers.values():
        updated_date = datetime.strptime(paper['updated_str'], "%Y-%m-%dT%H:%M:%SZ").date()
        if not (yesterday.date() <= updated_date <= today.date()):
            continue
        if not any(subject in paper['categories'] for subject in ALLOWED_SUBJECTS):
            continue
        filtered_papers.append(paper)
    return filtered_papers

def analyze_paper_with_gemini(paper):
    """
    Gemini를 사용하여 PDF 논문을 분석하고, 요약을 5개 항목으로 파싱하여 반환합니다.
    """
    global current_model_index

    # --- PDF 다운로드 ---
    try:
        print(f"  - PDF 다운로드 중: {paper['pdf_link']}")
        doc_response = httpx.get(paper['pdf_link'], timeout=30)
        doc_response.raise_for_status()
        doc_data = doc_response.content
        print("  - PDF 다운로드 완료.")
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        print(f"  ❌ PDF 다운로드/처리 실패: {e}")
        return None, None

    # --- Gemini 프롬프트 (항목별 태그 추가) ---
    prompt = f"""
    You are an AI assistant helping a researcher. Your task is to analyze the attached PDF paper and provide two outputs: an English summary divided into five specific sections, and an assessment of its relevance.

    **My Research Area:**
    "{MY_RESEARCH_AREA}"

    **Instructions:**

    1.  **Paper Summary (English):** Please summarize the paper, strictly following the five-part structure below. Use the exact tags `[MOTIVATION]`, `[DIFFERENCES]`, `[CONTRIBUTIONS]`, `[METHOD]`, `[RESULTS]` to label each section. Each section should be a concise paragraph.
        * `[MOTIVATION]`: What problem does this research aim to solve, and why is it important?
        * `[DIFFERENCES]`: How is this work different from or improving upon previous approaches?
        * `[CONTRIBUTIONS]`: What are the main contributions and novel aspects of this paper?
        * `[METHOD]`: What method or approach do the authors propose?
        * `[RESULTS]`: What are the key results that demonstrate the effectiveness of the proposed method?

    2.  **Relevance Assessment:** Please determine if the paper’s contributions are directly relevant to my research area.

    3.  **Output Format:** You **MUST** follow the exact format below, using "|||" as a delimiter. Do not include any additional commentary or greetings.

    **Output Format:**
    [MOTIVATION]
    ... summary ...
    [DIFFERENCES]
    ... summary ...
    [CONTRIBUTIONS]
    ... summary ...
    [METHOD]
    ... summary ...
    [RESULTS]
    ... summary ...
    |||[Yes. or No.]
    """

    while current_model_index < len(MODEL_LIST):
        model_to_use = MODEL_LIST[current_model_index]
        print(f"  - Gemini 분석 시도 (모델: {model_to_use})")
        

        try:
            response = client.models.generate_content(
                model=model_to_use,
                contents=[
                    types.Part.from_bytes(data=doc_data, mime_type='application/pdf'),
                    prompt
                ],
            )

            if response.text and '|||' in response.text:
                summary_part, answer_part = [p.strip() for p in response.text.strip().split('|||', 1)]
                
                # --- 정규표현식을 이용한 파싱 ---
                tags = ["MOTIVATION", "DIFFERENCES", "CONTRIBUTIONS", "METHOD", "RESULTS"]
                parsed_summary = {}
                for i in range(len(tags)):
                    current_tag = tags[i]
                    next_tag = tags[i+1] if i + 1 < len(tags) else None
                    
                    pattern = f"\[{current_tag}\](.*?)"
                    if next_tag:
                        # 다음 태그가 있으면, non-greedy 방식으로 다음 태그 직전까지 파싱
                        pattern = f"\[{current_tag}\](.*?)(?=\[{next_tag}\])"
                    else:
                        # 다음 태그가 없는 마지막 태그이면, greedy 방식으로 문자열 끝까지 파싱
                        pattern = f"\[{current_tag}\](.*)"
                    
                    match = re.search(pattern, summary_part, re.DOTALL | re.IGNORECASE)
                    
                    if match:
                        # 파싱된 내용의 길이가 2000자를 넘으면 잘라내기
                        content = match.group(1).strip()
                        parsed_summary[current_tag] = content[:1990] + '...' if len(content) > 2000 else content
                    else:
                        parsed_summary[current_tag] = "N/A" # 해당 섹션을 찾지 못한 경우

                # 모든 태그가 파싱되었는지 확인
                if all(tag in parsed_summary for tag in tags):
                    relevance = "Related" if "yes" in answer_part.lower() else "Unrelated"
                    return relevance, parsed_summary
            
            print(f"  ⚠️ Gemini가 예상치 못한 형식으로 답변: {response.text[:200]}...")
            return None, None

        except Exception as e:
            if "overload" in str(e).lower():
                print(f"  ⏳ 모델 '{model_to_use}' 과부하. 30초 후 재시도합니다.")
                time.sleep(30)
                continue
            else:
                if "resource_exhausted" in str(e).lower() or "quota" in str(e).lower():
                    print(f"  ⚠️ 모델 '{model_to_use}'의 API 쿼터 소진. 다음 모델로 전환합니다.")
                    current_model_index += 1
                    time.sleep(2)
                else:
                    print(f"  ❌ Gemini API 호출 중 예상치 못한 오류 발생: {e}")
                    return None, None

    print("  ❌ 사용 가능한 모든 Gemini 모델의 쿼터를 소진했습니다.")
    return None, None

# ✅ Notion에 논문 추가 (변경 없음 - 이미 요약본을 받도록 설계됨)
def add_to_notion(paper, related_status, summary_parts):
    """논문 정보, 관련도, 분할된 요약을 Notion에 추가합니다."""
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    updated_str = paper['updated_str'].split('T')[0]

    # Notion 속성 이름과 summary_parts의 키를 정확히 일치시켜야 합니다.
    # 예: Notion 속성 이름 'Motivation' -> summary_parts['MOTIVATION']
    properties = {
        "Paper": {"title": [{"text": {"content": paper['title']}}]},
        "Abstract": {"rich_text": [{"text": {"content": paper.get('abstract', '')}}]}, # 원본 초록 저장
        "Author": {"rich_text": [{"text": {"content": paper.get('author', 'arXiv')}}]},
        "Relatedness": {"select": {"name": related_status}},
        "URL": {"url": paper['link']},
        "Date": {"date": {"start": updated_str}},
        # --- 분할된 요약 추가 ---
        "Motivation": {"rich_text": [{"text": {"content": summary_parts.get('MOTIVATION', 'N/A')}}]},
        "Differences from Prior Work": {"rich_text": [{"text": {"content": summary_parts.get('DIFFERENCES', 'N/A')}}]},
        "Contributions and Novelty": {"rich_text": [{"text": {"content": summary_parts.get('CONTRIBUTIONS', 'N/A')}}]},
        "Proposed Method": {"rich_text": [{"text": {"content": summary_parts.get('METHOD', 'N/A')}}]},
        "Results": {"rich_text": [{"text": {"content": summary_parts.get('RESULTS', 'N/A')}}]}
    }

    data = {"parent": {"database_id": DATABASE_ID}, "properties": properties}

    try:
        res = requests.post(url, headers=headers, json=data, timeout=15)
        if res.status_code == 200:
            print(f"✅ Notion 등록 성공: {paper['title'][:60]}... (상태: {related_status})")
        else:
            print(f"❌ Notion 등록 실패: {paper['title'][:60]}...")
            print(f"📄 Notion 응답: {res.status_code}")
            print(res.text) # 실패 시 에러 메시지 확인
    except requests.exceptions.RequestException as e:
        print(f"❌ Notion API 요청 실패: {paper['title'][:60]}... | {e}")


def main():
    """메인 스크립트 실행 함수"""
    print("🚀 논문 자동화 스크립트를 시작합니다.")

    print("\n[1/4] 📚 Notion DB에서 기존 논문 목록 가져오는 중...")
    existing_titles = fetch_existing_titles()
    print(f"총 {len(existing_titles)}개의 논문이 Notion에 존재합니다.")

    print("\n[2/4] 🔍 arXiv에서 신규 논문 검색 및 필터링 중...")
    arxiv_papers = fetch_arxiv_papers()
    print(f"👍 날짜/주제 필터 통과한 논문 수: {len(arxiv_papers)}")

    final_papers_to_add = []
    if arxiv_papers:
        print("\n[3/4] 🤖 Gemini 관련도 분석 및 항목별 요약 시작...")
        new_papers = [p for p in arxiv_papers if p['title'] not in existing_titles]
        print(f"중복을 제외한 신규 논문 {len(new_papers)}개를 분석합니다.")

        for i, paper in enumerate(new_papers):
            print(f"({i+1}/{len(new_papers)}) 🔬 Gemini 분석 중: {paper['title'][:60]}...")
            
            # Gemini 함수가 (상태, 요약 딕셔너리)를 반환
            related_status, summary_parts = analyze_paper_with_gemini(paper)

            if related_status and summary_parts:
                # `paper` 객체, `status`, `summary_parts` 딕셔너리를 함께 저장
                final_papers_to_add.append((paper, related_status, summary_parts))
                print(f"👍 Gemini 분석 완료! (상태: {related_status})")
            else:
                print(f"👎 Gemini 분석 실패. 이 논문은 등록되지 않습니다.")
            time.sleep(1)

    print(f"\n[4/4] 📝 Notion DB에 최종 논문 등록 시작...")
    if not final_papers_to_add:
        print("✨ 새로 추가할 논문이 없습니다.")
    else:
        print(f"총 {len(final_papers_to_add)}개의 새로운 논문을 Notion에 추가합니다.")
        # `paper`, `status`, `parts`를 올바르게 전달
        for paper, status, parts in final_papers_to_add:
            add_to_notion(paper, status, parts)
            time.sleep(0.5)

    print("\n🎉 모든 작업이 완료되었습니다!")

if __name__ == "__main__":
    main()
