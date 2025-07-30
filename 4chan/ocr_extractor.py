import json
import requests
from PIL import Image
import pytesseract
from io import BytesIO
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# If tesseract binary isn't on your PATH, set this:
# pytesseract.pytesseract.tesseract_cmd = r'/usr/local/bin/tesseract'

def make_session_with_retries(
    total_retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (500, 502, 503, 504),
) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        read=total_retries,
        connect=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset(['GET', 'POST']),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

session = make_session_with_retries()

def extract_image_text_from_url(url: str) -> str:
    try:
        resp = session.get(url, timeout=10, verify=True)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        return pytesseract.image_to_string(img)
    except requests.exceptions.SSLError as ssl_err:
        print(f"🔒 SSL error downloading {url}: {ssl_err}")
    except requests.exceptions.RequestException as req_err:
        print(f"⚠️ Request error for {url}: {req_err}")
    except IOError as io_err:
        print(f"🖼️ Image decode error for {url}: {io_err}")
    except Exception as e:
        print(f"❓ Unexpected error for {url}: {e}")
    return ""

def add_ocr_to_dataset(input_path: str, output_path: str):
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for post in data:
        image_texts = []
        for img_url in post.get("image_urls", []):
            text = extract_image_text_from_url(img_url)
            if text.strip():
                image_texts.append(text.strip())
        post["ocr_text"] = "\n\n---\n\n".join(image_texts)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Wrote OCR results to {output_path}")

if __name__ == "__main__":
    # pip install pillow pytesseract requests urllib3
    add_ocr_to_dataset("4chan_full_data_images.json", "4chan/4chan_data_with_ocr.json")
