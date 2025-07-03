# Arxiv2Notion

Arxiv2Notion is an automated tool that searches for newly published papers on [arXiv.org](https://arxiv.org), checks their relevance to your research, and logs them neatly into your Notion database.

---

## 🔍 How It Works

- **Daily Search:**
  Runs every day at **9:00 AM KST** (customizable via the GitHub Actions workflow `.yml` file).

- **Keyword Matching:**
  Searches arXiv using a list of user-defined keywords in `arxiv_to_notion.py`. Keywords with hyphens (e.g., `few-shot`) are handled appropriately.

- **Smart Filtering:**
  Uses **Google Gemini models** to:
  - **Summarize abstracts**
  - **Determine relevance** to your research (`Related` / `Unrelated`)

- **Duplicate Handling:**
  Automatically filters out previously processed papers to avoid duplication.

---

## 🧠 Gemini Models

The following Gemini models are used in sequence to support higher request rates (up to 45 RPM or 1550 RPD depending on your tier):

---

## ⚙️ Configuration

The script can be configured via `arxiv_to_notion.py` and GitHub Action secrets.

### 🔐 GitHub Secrets
- You should register these 3 parameters in `Settings -> Secrets and variables -> New repository secret`
- How to get your database ID? ([Eng](https://stackoverflow.com/questions/67728038/where-to-find-database-id-for-my-database-in-notion)) | ([Kor](https://nyukist.tistory.com/16))
- How to get notion token? ([Eng](https://developers.notion.com/docs/create-a-notion-integration)) | ([Kor](https://newdeal123.tistory.com/86))


| Name                  | Description                          |
|-----------------------|--------------------------------------|
| `NOTION_TOKEN`        | Your Notion integration token(==Notion API token)|
| `DATABASE_ID`         | Target Notion database ID            |
| `GOOGLE_API_KEY`      | Google Gemini API key (free or paid) |

### 🛠 Script Parameters
- You should change these parameters in `arxiv_to_notion.py` file.

| Parameter          | Description                                                       |
|--------------------|-------------------------------------------------------------------|
| `LOOKBACK_DAYS`    | How many days back from today to search on arXiv                  |
| `KEYWORDS`         | List of keywords to search for                                    |
| `MY_RESEARCH_AREA` | A short description of your research area (used to check relevance) |

---

## 🗃️ Notion Table Structure

| Column     | Description                                   |
|------------|-----------------------------------------------|
| `Paper`    | Title of the paper                            |
| `Abstract`  | Shortened abstract (via Gemini)               |
| `Relatedness`  | Whether the paper is related to your research |
| `Date`| Publication date on arXiv                     |
| `URL`      | Direct link to the arXiv paper                |
| `Author`   | Authors of the paper                        |


---

## 🛠 Setup Instructions

1. **Fork/clone** this repository.
2. Set the following secrets in **GitHub → Settings → Secrets and variables → Actions**:
   - `NOTION_TOKEN`
   - `DATABASE_ID`
   - `GOOGLE_API_KEY`
3. Modify `KEYWORDS`, `LOOKBACK_DAYS`, and `MY_RESEARCH_AREA` in `arxiv_to_notion.py`.
4. That's it! The script will run daily via GitHub Actions.

---

## 📅 Scheduling

- Default: **Every day at 09:00 AM KST**
- To change the schedule, edit the cron expression in `.github/workflows/main.yml`.

---
