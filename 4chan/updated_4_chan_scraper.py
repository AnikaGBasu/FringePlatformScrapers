from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import json
import traceback
import time # Import time for potential delays between pages

def get_thread_urls_from_archive_page(archive_url):
    """
    Navigates to a 4plebs archive page and extracts the URLs of individual threads.
    
    Args:
        archive_url (str): The URL of the 4plebs archive page (e.g., a timetravel page).

    Returns:
        list: A list of individual thread URLs found on the archive page.
    """
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("window-size=1920,1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    thread_urls = []
    try:
        driver.get(archive_url)

        # dismiss cookie banner
        driver.execute_script(
            """
          const b = document.getElementById('cookies-eu-banner');
          if (b) b.style.display = 'none';
        """
        )

        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "article.clearfix.thread"))
        )

        threads = driver.find_elements(By.CSS_SELECTOR, "article.clearfix.thread")
        
        for th in threads:
            post_id = th.get_attribute("id")
            if post_id:
                # Construct the full thread URL from the post ID
                # 4plebs thread URLs are typically /board/thread/ID/
                thread_url = f"https://archive.4plebs.org/pol/thread/{post_id}/"
                thread_urls.append(thread_url)

        print(f"Collected {len(thread_urls)} thread URLs from {archive_url}")
        return thread_urls

    except Exception:
        print(f"\n[!] An error occurred while getting thread URLs from {archive_url}. Returning empty data.")
        traceback.print_exc()
        return []

    finally:
        driver.quit()

def scrape_pol_thread(thread_url):
    """
    Navigates to a single 4plebs thread URL and scrapes all its content,
    including the original post and all replies.

    Args:
        thread_url (str): The direct URL to a specific 4plebs thread.

    Returns:
        dict: A dictionary containing the scraped data for the thread,
              or an empty dictionary if an error occurs.
    """
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("window-size=1920,1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(thread_url)

        # dismiss cookie banner
        driver.execute_script(
            """
          const b = document.getElementById('cookies-eu-banner');
          if (b) b.style.display = 'none';
        """
        )

        # Wait for the main thread post to be present
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "article.clearfix.thread"))
        )

        # Get the main thread element (OP)
        th = driver.find_element(By.CSS_SELECTOR, "article.clearfix.thread")
        
        thread_data = {}

        def fix_image_urls(urls):
            return [url.replace("s.jpg", ".jpg") if url.endswith("s.jpg") else url for url in urls]

        op = {}
        post_id = th.get_attribute("id")
        op["post_id"] = post_id
        op["post_url"] = thread_url # The input URL is the post URL

        try:
            op["author_id"] = th.find_element(
                By.CSS_SELECTOR, "span.poster_hash"
            ).text.replace("ID:", "").strip()
        except:
            op["author_id"] = None

        try:
            op["text"] = th.find_element(
                By.CSS_SELECTOR, "div.text"
            ).text.strip()
        except:
            op["text"] = None

        try:
            tm = th.find_element(By.CSS_SELECTOR, "time")
            op["timestamp_raw"] = tm.text
            op["timestamp_iso"] = tm.get_attribute("datetime")
        except:
            op["timestamp_raw"] = op["timestamp_iso"] = None

        imgs = th.find_elements(By.CSS_SELECTOR, "img.thread_image, img.post_image")
        op["image_urls"] = fix_image_urls([img.get_attribute("src") for img in imgs])

        # Scrape all replies (they should be fully loaded on a direct thread page)
        reply_articles = th.find_elements(By.CSS_SELECTOR, "aside.posts article")
        op["reply_count"] = len(reply_articles)
        op["replies"] = []
        for ra in reply_articles:
            r = {}
            r["reply_id"] = ra.get_attribute("id")
            try:
                r["reply_text"] = ra.find_element(
                    By.CSS_SELECTOR, "div.text"
                ).text.strip()
            except:
                r["reply_text"] = None
            try:
                rt = ra.find_element(By.CSS_SELECTOR, "time")
                r["reply_timestamp_raw"] = rt.text
                r["reply_timestamp_iso"] = rt.get_attribute("datetime")
            except:
                r["reply_timestamp_raw"] = r["reply_timestamp_iso"] = None
            rimgs = ra.find_elements(By.CSS_SELECTOR, "img.post_image")
            r["image_urls"] = fix_image_urls([img.get_attribute("src") for img in rimgs])
            op["replies"].append(r)

        thread_data = op # The main thread data itself contains the replies

        print(f"Scraped thread: {thread_url} with {op['reply_count']} replies.")
        return thread_data

    except Exception:
        print(f"\n[!] An error occurred while scraping thread: {thread_url}. Returning empty data.")
        traceback.print_exc()
        return {}

    finally:
        driver.quit()

if __name__ == "__main__":
    # Define the base archive URL and the desired number of posts
    base_archive_url = "https://archive.4plebs.org/pol/timetravel/2025-07-07_18:34:00/"
    target_posts = 1000
    posts_per_page = 10 
    num_pages_to_scrape = (target_posts + posts_per_page - 1) // posts_per_page # Ceiling division

    all_scraped_data = [] # Initialize here so it's accessible everywhere

    try:
        print(f"Starting to scrape {target_posts} posts across {num_pages_to_scrape} pages from {base_archive_url}")

        for page_num in range(1, num_pages_to_scrape + 1):
            if len(all_scraped_data) >= target_posts:
                print(f"Target of {target_posts} posts reached. Stopping.")
                break

            # Construct the URL for the current archive page
            if page_num == 1:
                current_archive_page_url = base_archive_url
            else:
                current_archive_page_url = f"{base_archive_url.rstrip('/')}/{page_num}/"

            print(f"\nStep 1: Getting thread URLs from archive page: {current_archive_page_url} (Page {page_num}/{num_pages_to_scrape})")
            thread_urls_on_page = get_thread_urls_from_archive_page(current_archive_page_url)

            if not thread_urls_on_page:
                print(f"No thread URLs found on page {page_num}. Ending pagination.")
                break

            print(f"Step 2: Scraping data for {len(thread_urls_on_page)} threads from Page {page_num}...")
            for i, thread_url in enumerate(thread_urls_on_page):
                if len(all_scraped_data) >= target_posts:
                    print(f"Target of {target_posts} posts reached during page {page_num} processing. Stopping.")
                    break
                
                print(f"Processing thread {i+1}/{len(thread_urls_on_page)} on page {page_num}: {thread_url}")
                data = scrape_pol_thread(thread_url)
                if data:
                    all_scraped_data.append(data)
                else:
                    print(f"Skipping empty data for thread: {thread_url}")
                
                # Optional: Add a small delay between individual thread scrapes to be polite
                # time.sleep(0.5) 

    except KeyboardInterrupt:
        print("\n[!] Keyboard interrupt detected. The data will be saved in the finally block.")
        # Re-raise the KeyboardInterrupt so the program terminates after finally
        raise 
    except Exception:
        error_trace = traceback.format_exc()
        print(f"\n[!] A non-KeyboardInterrupt error occurred. Traceback:\n{error_trace}")
        # The finally block will still handle the saving
        
    finally:
        # This block will always execute, ensuring data saving on normal exit or interrupt
        if all_scraped_data:
            output_filename = "pol_archive_all_threads_data.json"
            # Ensure we only save up to target_posts if more were accidentally collected
            final_data_to_save = all_scraped_data[:target_posts] 
            try:
                with open(output_filename, "w") as f:
                    json.dump(final_data_to_save, f, indent=2)
                print(f"\nSuccessfully saved partial/final data for {len(final_data_to_save)} threads to {output_filename}")
            except Exception as save_err:
                print(f"\n[!] Error saving data in finally block: {save_err}")
        else:
            print("\nNo thread data was collected to save.")