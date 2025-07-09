import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import openai
import time
import io
import re
from datetime import datetime
import fitz  # PyMuPDF for PDF extraction

# Load OpenAI API Key securely
openai.api_key = st.secrets["OPENAI_API_KEY"]

st.set_page_config(page_title="2l Filing Extractor", layout="wide")
st.title("📄 2l Filing Extractor with PDF Support")

# ---------------- PDF + Web Text Fetching ----------------

def fetch_clean_text(url):
    try:
        if url.lower().endswith(".pdf"):
            # Download and extract PDF text
            response = requests.get(url, timeout=10)
            with open("temp.pdf", "wb") as f:
                f.write(response.content)
            doc = fitz.open("temp.pdf")
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text[:4000] if text else "Error: No text found in PDF"
        else:
            # Fallback for HTML links
            res = requests.get(url, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            for tag in soup(["script", "style"]): tag.decompose()
            return soup.get_text(separator=" ", strip=True)[:4000]
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"

# ---------------- 2l Prompt + Parsing ----------------

def generate_2l_format(text):
    prompt = f"""
You are an expert equity research analyst. Given the following content from a web page or PDF, extract the information and present it in this custom format called '2l':

1. Key pointers and very important
2. Summarize this (if possible, add % with this)
3. A 2-3 line final summary (if possible, add % with this)
4. Explain to a 5-year-old
5. In one word (a proper heading with process)
6. Is it good or bad for the company (in 2 lines)

Only output in structured format. Do not explain.

Here is the content:
{text}
"""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        return response['choices'][0]['message']['content']
    except Exception as e:
        return f"Error: {e}"

def parse_2l(response):
    lines = response.split("\n")
    values = [""] * 6
    for line in lines:
        line = line.strip()
        if line.startswith("1."): values[0] = line.partition(".")[2].strip()
        elif line.startswith("2."): values[1] = line.partition(".")[2].strip()
        elif line.startswith("3."): values[2] = line.partition(".")[2].strip()
        elif line.startswith("4."): values[3] = line.partition(".")[2].strip()
        elif line.startswith("5."): values[4] = line.partition(".")[2].strip()
        elif line.startswith("6."): values[5] = line.partition(".")[2].strip()
    return values

# ---------------- Metadata Extraction ----------------

def extract_company(text):
    match = re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+)* (?:Limited|Ltd|Industries|Corporation))", text)
    return match.group(0) if match else "Unknown"

def guess_sector(text):
    keywords = {
        "Pharma": "Healthcare",
        "Chemical": "Specialty Chemicals",
        "Bank": "Financials",
        "Power": "Energy",
        "Steel": "Metals",
        "Auto": "Automobile",
        "IT": "Technology",
        "Software": "Technology",
        "Retail": "Consumer",
        "FMCG": "Consumer Staples",
    }
    for k, v in keywords.items():
        if k.lower() in text.lower():
            return v
    return "Unknown"

def extract_symbol(url, text):
    match = re.search(r'/([A-Z]{1,10})_', url)
    if match:
        return match.group(1)
    match = re.search(r'/companies/([A-Z]{1,10})', url)
    if match:
        return match.group(1)
    match = re.search(r'BSE[:\s]+([A-Z]{1,10})', text)
    if match:
        return match.group(1)
    return "Unknown"

def detect_announcement_type(text):
    keywords = {
        "expansion": "Expansion",
        "capex": "Capex",
        "dividend": "Dividend",
        "merger": "Merger/Acquisition",
        "acquisition": "Merger/Acquisition",
        "order": "Order Win",
        "contract": "Order Win",
        "plant": "Capacity/Infra",
        "result": "Financial Result",
        "profit": "Financial Result",
        "loss": "Financial Result",
        "bonus": "Bonus/Split",
        "buyback": "Buyback",
        "joint venture": "JV/Partnership",
    }
    for k, v in keywords.items():
        if k in text.lower():
            return v
    return "General"

def extract_date_from_url(url):
    match = re.search(r'(\d{4})[-/]?(\d{2})[-/]?(\d{2})', url)
    if match:
        y, m, d = match.groups()
        return f"{y}-{m}-{d}"
    return datetime.today().strftime("%Y-%m-%d")

# ---------------- Streamlit App UI ----------------

uploaded_file = st.file_uploader("📄 Upload CSV file with 'link' column", type="csv")

if uploaded_file:
    df_links = pd.read_csv(uploaded_file)

    if 'link' not in df_links.columns:
        st.error("CSV must contain a 'link' column.")
    else:
        if st.button("🔍 Run Extraction"):
            output_data = []
            progress = st.progress(0)
            status = st.empty()

            for i, row in df_links.iterrows():
                link = row['link']
                status.text(f"Processing {i+1}/{len(df_links)}: {link}")
                text = fetch_clean_text(link)

                if text.startswith("Error"):
                    output_data.append([link, "Unknown", "Unknown", "Unknown", "Error", "Error"] + ["Error"]*6)
                    continue

                date = extract_date_from_url(link)
                company = extract_company(text)
                sector = guess_sector(text)
                symbol = extract_symbol(link, text)
                ann_type = detect_announcement_type(text)

                gpt_output = generate_2l_format(text)
                values = parse_2l(gpt_output)

                output_data.append([link, symbol, company, sector, date, ann_type] + values)
                progress.progress((i+1)/len(df_links))
                time.sleep(1.2)

            columns = ["Link", "Symbol", "Company", "Sector", "Date", "Announcement Type",
                       "Key Pointers", "Summary", "Final Summary",
                       "Explain Like 5", "One Word", "Good/Bad"]
            df_out = pd.DataFrame(output_data, columns=columns)

            st.success("✅ Extraction complete!")
            st.dataframe(df_out)

            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df_out.to_excel(writer, index=False, sheet_name="2l Summary")
            st.download_button("📥 Download Excel",
                               data=buffer.getvalue(),
                               file_name="2l_summary.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
