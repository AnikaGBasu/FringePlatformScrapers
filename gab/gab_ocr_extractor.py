import json
import requests
from PIL import Image
from io import BytesIO
import pytesseract

# Paths
INPUT_JSON_PATH = "final_gab_data.json"
OUTPUT_JSON_PATH = "gab_data_with_ocr.json"

def ocr_from_url(url):
    try:
        response = requests.get(url, timeout=10)
        image = Image.open(BytesIO(response.content))
        return pytesseract.image_to_string(image)
    except Exception as e:
        return f"[OCR Error] {str(e)}"

def process_data(data):
    total_posts = len(data)
    for idx, post in enumerate(data, start=1):
        # progress print
        print(f"{idx}/{total_posts} complete", flush=True)

        # Modify post["image_urls"] to store both URL and OCR result
        post_images = post.get("image_urls", [])
        new_post_images = []
        for url in post_images:
            text = ocr_from_url(url)
            new_post_images.append({
                "url": url,
                "ocr_text": text
            })
        post["image_urls"] = new_post_images

        # Process replies
        for reply in post.get("replies", []):
            reply_images = reply.get("image_urls", [])
            reply["reply_ocr_text"] = []
            for url in reply_images:
                text = ocr_from_url(url)
                reply["reply_ocr_text"].append({
                    "url": url,
                    "ocr_text": text
                })

    return data

def main():
    with open(INPUT_JSON_PATH, "r") as infile:
        data = json.load(infile)

    updated_data = process_data(data)

    with open(OUTPUT_JSON_PATH, "w") as outfile:
        json.dump(updated_data, outfile, indent=2)

    print(f"OCR completed. Results saved to {OUTPUT_JSON_PATH}")

if __name__ == "__main__":
    main()
