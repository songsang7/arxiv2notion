import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from google import genai
import time
from google.genai import types
import httpx

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("DATABASE_ID")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# ✅ Hard-code non-secret configurations directly in the script
KEYWORDS = [
    "audio language model",
    "speech language model",
    "speech style",
    "spoken language model",
    "speech to speech",
    "audio to speech",
    "Omni",
    "voice assistant"
  ]
ALLOWED_SUBJECTS = {"cs.CL", "cs.AI", "cs.LG"}
MY_RESEARCH_AREA = "My research focuses on developing virtual agents that understand user situations by jointly reasoning over user speech and ambient sounds as multimodal input, with a particular emphasis on generating speech with diverse styles using audio language models."
LOOKBACK_DAYS = 3

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
    Gemini를 사용하여 PDF 논문을 분석하고, 한국어 요약과 관련도를 반환합니다.
    API 쿼터 소진 시, 자동으로 다음 모델로 전환하여 재시도합니다.
    """
    global current_model_index

    # --- PDF 다운로드 ---
    pdf_url = paper['pdf_link']
    try:
        print(f"    - PDF 다운로드 중: {pdf_url}")
        doc_response = httpx.get(pdf_url, timeout=30)
        doc_response.raise_for_status()
        doc_data = doc_response.content
        print("    - PDF 다운로드 완료.")
    except httpx.RequestError as e:
        print(f"    ❌ PDF 다운로드 실패: {e}")
        return None, None
    except httpx.HTTPStatusError as e:
        print(f"    ❌ PDF를 찾을 수 없거나 서버 오류: {e}")
        return None, None

    # --- Gemini 프롬프트 ---
    prompt = f"""
    당신은 연구원을 돕는 AI 조수입니다. 당신의 임무는 첨부된 PDF 논문을 분석하여 두 가지 결과물을 제공하는 것입니다: 한국어 요약, 그리고 나의 연구 분야와의 관련성 판단.

    **나의 연구 분야:**
    "{MY_RESEARCH_AREA}"

    **지시사항:**
    1.  **논문 요약 (한국어):** 논문의 핵심 내용을 한국어로 요약해 주세요. 요약에는 다음 내용이 반드시 포함되어야 합니다:
        * **Motivation:** 이 연구가 해결하고자 하는 문제는 무엇이며, 왜 중요한가?
        * **Proposed Method:** 문제를 해결하기 위해 저자들이 제안하는 새로운 방법론이나 접근 방식은 무엇인가? 기존 방법들과의 차이점은 무엇인가?
        * **Results:** 제안된 방법의 효과를 보여주는 주요 결과는 무엇인가?
        * **작성 스타일:** 불필요한 이모티콘이나 특수문자 없이, 완전한 문장으로 구성된 줄글 형태로 작성해 주세요.

    2.  **관련성 판단:** 논문의 기여가 나의 연구 분야에 직접적으로 관련이 있는지 평가해 주세요.

    3.  **출력 형식:** 반드시 아래 형식을 정확히 지켜서 응답해야 하며, "|||"를 구분자로 사용해야 합니다. 다른 추가적인 설명이나 인사말을 포함하지 마세요.

    **출력 형식:**
    [여기에 한국어 요약을 작성하세요.]|||[Yes. 또는 No.]
    """

    while current_model_index < len(MODEL_LIST):
        model_to_use = MODEL_LIST[current_model_index]
        print(f"    - Gemini 분석 시도 (모델: {model_to_use})")

        try:
            # API 호출 (PDF 데이터와 프롬프트를 함께 전송)
            response = client.models.generate_content(
                model = model_to_use,
                contents=[
                    types.Part.from_data(
                        data=doc_data,
                        mime_type='application/pdf',
                    ),
                    prompt
                ]
            )

            # 응답 처리
            if response.text and '|||' in response.text:
                parts = response.text.strip().split('|||')
                if len(parts) == 2:
                    summary = parts[0].strip()
                    answer_part = parts[1].strip().lower()
                    if "yes" in answer_part:
                        return "Related", summary
                    elif "no" in answer_part:
                        return "Unrelated", summary

            print(f"    ⚠️ Gemini가 예상치 못한 형식으로 답변: {response.text}...")
            return None, None

        except Exception as e:
            error_message = str(e).lower()
            if "resource_exhausted" in error_message or "quota" in error_message:
                print(f"    ⚠️ 모델 '{model_to_use}'의 API 쿼터 소진. 다음 모델로 전환합니다.")
                current_model_index += 1
                time.sleep(2)
                continue
            else:
                print(f"    ❌ Gemini API 호출 중 예상치 못한 오류 발생: {e}")
                return None, None

    print("    ❌ 사용 가능한 모든 Gemini 모델의 쿼터를 소진했습니다. 분석을 중단합니다.")
    return None, None

# ✅ Notion에 논문 추가 (변경 없음 - 이미 요약본을 받도록 설계됨)
def add_to_notion(paper, related_status):
    """논문 정보, 관련도 상태, 발행일을 Notion에 추가합니다."""
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # ✨ arXiv의 'updated' 날짜(예: 2025-07-02T10:00:00Z)에서 'YYYY-MM-DD' 부분만 추출
    updated_str = paper['updated_str'].split('T')[0]

    properties = {
        "Paper": {"title": [{"text": {"content": paper['title']}}]},
        "Abstract": {"rich_text": [{"text": {"content": paper.get('abstract', '')}}]},
        "Author": {"rich_text": [{"text": {"content": paper.get('author', 'arXiv')}}]},
        "Relatedness": {"select": {"name": related_status}},
        "url": {"url": paper['link']},
        # ✨ 'Date' 속성에 추출한 날짜를 추가하는 부분
        "Date": {
            "date": {
                "start": updated_str
            }
        }
    }

    data = {
        "parent": {"database_id": DATABASE_ID},
        "properties": properties
    }

    try:
        res = requests.post(url, headers=headers, json=data, timeout=10)
        # 성공(200)이든 실패든 응답 내용을 출력하도록 수정
        print(f"📄 Notion 응답: {res.status_code}")
        print(res.text) # Notion이 보내준 상세 응답 내용 확인

        if res.status_code == 200:
            print(f"✅ Notion 등록 성공: {paper['title'][:60]}... (상태: {related_status})")
        else:
            print(f"❌ Notion 등록 실패: {paper['title'][:60]}...")
    except requests.exceptions.RequestException as e:
        print(f"❌ Notion API 요청 실패: {paper['title'][:60]}... | {e}")


def main():
    """메인 스크립트 실행 함수"""
    print("🚀 논문 자동화 스크립트를 시작합니다.")

    # 1. Notion DB에서 기존 논문 목록 가져오기
    print("\n[1/4] 📚 Notion DB에서 기존 논문 목록 가져오는 중...")
    existing_titles = fetch_existing_titles()
    print(f"총 {len(existing_titles)}개의 논문이 Notion에 존재합니다.")

    # 2. arXiv에서 신규 논문 검색 및 필터링
    print("\n[2/4] 🔍 arXiv에서 신규 논문 검색 및 필터링 중...")
    arxiv_papers = fetch_arxiv_papers()
    print(f"👍 날짜/주제 필터 통과한 논문 수: {len(arxiv_papers)}")

    # 3. Gemini 필터링 및 최종 중복 검사
    final_papers_to_add = []
    if arxiv_papers:
        print("\n[3/4] 🤖 Gemini 관련도 분석 및 초록 요약 시작...")
        new_papers = []
        for paper in arxiv_papers:
            # ✨ 공백 정규화된 제목으로 중복 검사
            if paper['title'] not in existing_titles:
                new_papers.append(paper)

        print(f"중복을 제외한 신규 논문 {len(new_papers)}개를 분석합니다.")

        for i, paper in enumerate(new_papers):
            print(f"({i+1}/{len(new_papers)}) 🔬 Gemini 분석 중: {paper['title'][:60]}...")
            # ✨ Gemini 함수가 이제 2개의 값을 반환 (상태, 요약본)
            related_status, summarized_abstract = analyze_paper_with_gemini(paper)

            if related_status and summarized_abstract:
                # ✨ paper 객체의 abstract를 요약본으로 교체
                paper['abstract'] = summarized_abstract
                final_papers_to_add.append((paper, related_status))
                print(f"👍 Gemini 분석 완료! (상태: {related_status})")
            else:
                print(f"👎 Gemini 분석 실패. 이 논문은 등록되지 않습니다.")
            time.sleep(1) # Gemini API 과호출 방지

    # 4. 최종 목록을 Notion에 추가
    print(f"\n[4/4] 📝 Notion DB에 최종 논문 등록 시작...")
    if not final_papers_to_add:
        print("✨ 새로 추가할 논문이 없습니다.")
    else:
        print(f"총 {len(final_papers_to_add)}개의 새로운 논문을 Notion에 추가합니다.")
        for paper, status in final_papers_to_add:
            add_to_notion(paper, status)
            time.sleep(0.5) # Notion API 속도 제한 고려

    print("\n🎉 모든 작업이 완료되었습니다!")


if __name__ == "__main__":
    main()
