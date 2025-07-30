import sys
import time
from datetime import datetime, timezone, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, TimeoutException, StaleElementReferenceException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import json
from urllib.parse import urljoin
import re

# -- Configuration --
POSTS_TO_SCRAPE = 1000 # Keep this low for testing, user can adjust. Script will continue until this many *valid* posts are found.
SCROLL_PAUSE_TIME = 2
MAX_SCROLLS = 500 # Increased max scrolls to allow deeper search for old posts
MIN_POST_AGE_DAYS = 0 # Post must be at least 14 days old
MIN_REPLIES_REQUIRED = 0 # Post must have at least 20 replies

# Custom Expected Condition to handle multiple possible conditions
class any_of_conditions:
    def __init__(self, *conditions):
        self.conditions = conditions

    def __call__(self, driver):
        for condition in self.conditions:
            try:
                result = condition(driver)
                if result:
                    return result
            except Exception: # Catch any exception, including NoSuchElementException, to let other conditions run
                pass
        return False

# Helper function to extract numerical count from a string
def extract_count_from_string(text_to_parse):
    """
    Extracts a numerical count from a string.
    Handles text like '1.2K', '345', '1,234', or numbers embedded in text.
    """
    if not text_to_parse:
        return 0

    try:
        # Regex to find numbers, potentially with commas or decimals, and optional K/M/B/T suffix
        # Added \s* to handle potential spaces around the number before K/M/B/T
        match = re.search(r'(\d[\d,.]*\s*[KMBTkmb]?)', text_to_parse)
        if match:
            num_str = match.group(1).replace(',', '').strip()
            
            # Handle K, M, B, T suffixes
            if 'K' in num_str.upper():
                return int(float(num_str.replace('K', '')) * 1000)
            elif 'M' in num_str.upper():
                return int(float(num_str.replace('M', '')) * 1000000)
            elif 'B' in num_str.upper():
                return int(float(num_str.replace('B', '')) * 1000000000)
            elif 'T' in num_str.upper():
                return int(float(num_str.replace('T', '')) * 1000000000000)
            else:
                return int(float(num_str))
    except (ValueError, AttributeError):
        pass
    return 0

# Helper function to parse Gab's specific datetime format
def parse_gab_datetime(dt_string):
    """
    Parses a datetime string from Gab (e.g., "Mon Jul 07 2025 07:59:42 GMT-0700 (Pacific Daylight Time)")
    into an ISO 8601 formatted string.
    """
    if not dt_string:
        return None # Return None if string is empty
    
    # Remove the parenthesized timezone part, e.g., "(Pacific Daylight Time)"
    clean_dt_string = re.sub(r'\s*\([^)]+\)', '', dt_string).strip()
    try:
        # Format string: "%a %b %d %Y %H:%M:%S GMT%z"
        # Example: "Mon Jul 07 2025 07:59:42 GMT-0700"
        dt_object = datetime.strptime(clean_dt_string, "%a %b %d %Y %H:%M:%S GMT%z")
        return dt_object.isoformat()
    except ValueError:
        return None # Return None on parse error


def scroll_to_end(driver, scroll_pause_time=SCROLL_PAUSE_TIME):
    """Scrolls down the page to load more content, for infinite scrolling feeds."""
    print(f"Initiating scroll sequence to load more content...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(scroll_pause_time)
    new_height = driver.execute_script("return document.body.scrollHeight")
    if new_height == last_height:
        print(f"  Reached end of scrollable content or no new content loaded.")
        return False # Indicate no new content was loaded
    else:
        print(f"  Scrolled. New page height: {new_height}px")
        return True # Indicate new content was loaded

# Helper function to scroll within a specific element
def scroll_element_to_end(driver, element_xpath, scroll_pause_time=1, max_scrolls=5):
    """Scrolls a specific element (e.g., a comments section) to load more content."""
    try:
        scroll_container = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, element_xpath))
        )
        print(f"  Attempting to scroll element: {element_xpath}")
        last_height = driver.execute_script("return arguments[0].scrollHeight", scroll_container)
        scroll_count = 0
        while scroll_count < max_scrolls:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", scroll_container)
            time.sleep(scroll_pause_time)
            new_height = driver.execute_script("return arguments[0].scrollHeight", scroll_container)
            if new_height == last_height:
                print(f"    Reached end of scrollable element or no new content after {scroll_count + 1} scrolls.")
                break
            last_height = new_height
            scroll_count += 1
            print(f"    Scrolled element {scroll_count} times. Element height: {new_height}px")
        print("  Finished scrolling element.")
        return True
    except TimeoutException:
        print(f"  Scrollable element not found or timed out: {element_xpath}")
        return False
    except Exception as e:
        print(f"  Error scrolling element {element_xpath}: {e}")
        return False

def get_post_details(post_url, driver):
    """Navigates to a specific post URL to scrape its full details and replies."""
    print(f"\n  Navigating to post detail page: {post_url}")
    try:
        driver.get(post_url)
        time.sleep(3) # Increased initial wait for full page render

        post_id = post_url.split('/')[-1].split('?')[0].split('#')[0]

        # Explicitly wait for the main post element to be present.
        WebDriverWait(driver, 25).until( 
            EC.presence_of_element_located((By.CSS_SELECTOR, f"div[data-id='{post_id}']"))
        )
        print(f"  Main post container (data-id='{post_id}') found on detail page.")
    except TimeoutException:
        print(f"  Error: Timed out waiting for main post container (data-id) on post page. Content might not be visible: {post_url}")
        return None
    except WebDriverException as e:
        print(f"  WebDriver error loading {post_url}: {e.msg if e.msg else str(e)}")
        return None
    except Exception as e:
        print(f"  An unexpected error occurred while loading {post_url}: {e}")
        return None

    post_data = {"post_id": post_id, "post_url": post_url, "image_urls": [], "replies": []}
    main_post_element = None

    try:
        main_post_element = driver.find_element(By.CSS_SELECTOR, f"div[data-id='{post_id}']")
        print("  Main post element successfully located for scraping.")

        # Scrape author username from URL
        try:
            print("    Attempting to scrape author username from URL...")
            match = re.search(r'gab.com/([^/]+)/posts/', post_url)
            if match:
                post_data["author_username"] = "@" + match.group(1)
            else:
                post_data["author_username"] = "N/A (Author not found in URL pattern)"
            print(f"    Author username: {post_data['author_username']}")
        except Exception as e:
            print(f"    Error scraping author username from URL: {e}")
            post_data["author_username"] = "Error (URL parse failed)"


        # Scrape post text (Revised for robustness against picking up header info)
        try:
            print("    Attempting to scrape post text...")
            text_element = WebDriverWait(main_post_element, 10).until(
                any_of_conditions(
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-content")]//span[@data-text-content]')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-content")]//div[@data-text-content]')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-content")]//p')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-content-body")]')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "break-words") and not(contains(@class, "post-header")) and not(contains(@class, "flex")) and not(contains(@class, "text-sm"))]')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-content")]')), 
                    EC.presence_of_element_located((By.XPATH, './/p[string-length(normalize-space(.)) > 10 and not(ancestor::div[contains(@class, "post-header")])]')),
                    EC.presence_of_element_located((By.XPATH, './/div[not(contains(@class, "hidden")) and string-length(normalize-space(.)) > 10 and not(contains(@class, "meta")) and not(contains(@class, "header")) and not(contains(@class, "text-gray"))]')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-description")]'))
                )
            )
            raw_text = text_element.text.strip()
            
            is_likely_header = False
            if len(raw_text) < 30 and (("·" in raw_text or "\n" in raw_text) and not re.search(r'[a-zA-Z]{5,}', raw_text)): 
                is_likely_header = True
            if raw_text.startswith(post_data["author_username"]) and len(raw_text) < 50:
                 is_likely_header = True

            if is_likely_header:
                 post_data["text"] = "No primary text content found."
                 print("    Detected header-like info in text, setting to 'No primary text content found.'.")
            else:
                 post_data["text"] = raw_text
            
            if len(post_data["text"]) > 100:
                print(f"    Post text (first 100 chars): {post_data['text'][:100]}...")
            else:
                print(f"    Post text: {post_data['text']}")

        except TimeoutException:
            post_data["text"] = "No primary text content found (timed out)."
            print("    Warning: Post text content element timed out.")
        except NoSuchElementException:
            post_data["text"] = "No primary text content found."
            print("    Warning: Post text content not found.")
        except Exception as e:
            print(f"    Error scraping post text: {e}")
            post_data["text"] = "Error retrieving text."


        # Scrape image URLs using regex on the main_post_element's outerHTML
        try:
            print("    Attempting to scrape image URLs from main post element's HTML...")
            main_post_html = main_post_element.get_attribute('outerHTML')
            image_urls = []
            img_src_pattern = re.compile(r'src="(https?://m3\.gab\.com/media_attachments/[^"]+\.(?:jpg|jpeg|png|gif|webp)[^"]*)"')
            
            for match in img_src_pattern.finditer(main_post_html):
                image_urls.append(match.group(1))
            
            post_data["image_urls"] = list(set(image_urls))
            print(f"    Found {len(post_data['image_urls'])} image URLs.")
        except Exception as e:
            post_data["image_urls"] = []
            print(f"    Error scraping image URLs from main post element: {e}")


        # Scrape timestamp and apply age filter
        try:
            print("    Attempting to scrape timestamp and apply age filter...")
            timestamp_elem = WebDriverWait(main_post_element, 10).until(
                any_of_conditions(
                    EC.presence_of_element_located((By.TAG_NAME, 'time')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-meta")]//time')),
                    EC.presence_of_element_located((By.XPATH, './/a[contains(@href, "/posts/") and contains(@href, "/posts/")]//time')),
                    EC.presence_of_element_located((By.XPATH, './/span[contains(@class, "timestamp")]'))
                )
            )
            raw_timestamp = timestamp_elem.get_attribute('datetime') or timestamp_elem.text.strip()
            post_data["timestamp"] = raw_timestamp
            post_data["timestamp_iso"] = parse_gab_datetime(raw_timestamp)
            print(f"    Timestamp: {post_data['timestamp']}")
            print(f"    Timestamp ISO: {post_data['timestamp_iso']}")

            # Age filter check
            if post_data["timestamp_iso"]:
                post_datetime = datetime.fromisoformat(post_data["timestamp_iso"])
                # Ensure comparison is timezone-aware
                if post_datetime.tzinfo is None:
                    post_datetime = post_datetime.replace(tzinfo=timezone.utc)
                
                current_datetime_utc = datetime.now(timezone.utc)
                age_difference = current_datetime_utc - post_datetime

                if age_difference.days < MIN_POST_AGE_DAYS:
                    print(f"    Post is only {age_difference.days} days old, which is less than {MIN_POST_AGE_DAYS} days. Skipping post.")
                    return None
            else:
                print("    Warning: Could not parse post timestamp. Cannot apply age filter. Skipping post to be safe.")
                return None

        except TimeoutException:
            post_data["timestamp"] = "N/A (Timeout)"
            post_data["timestamp_iso"] = "N/A"
            print("    Warning: Timestamp element timed out. Cannot apply age filter. Skipping post.")
            return None
        except NoSuchElementException:
            post_data["timestamp"] = "N/A"
            post_data["timestamp_iso"] = "N/A"
            print("    Warning: Timestamp not found for main post. Cannot apply age filter. Skipping post.")
            return None
        except Exception as e:
            print(f"    Error scraping timestamp or applying age filter: {e}. Skipping post.")
            post_data["timestamp"] = "Error"
            post_data["timestamp_iso"] = "Error"
            return None

        # Scrape reactions, reposts, quotes, and views from the entire page source using regex
        print("    Attempting to scrape interactions (reactions, reposts, quotes, views) from page source...")
        page_source = driver.page_source
        
        # Reactions (formerly 'likes')
        reactions_match = re.search(r'<span\s+class="_3u7ZG\s+_UuSG\s+_3_54N\s+a8-QN\s+_2cSLK\s+L4pn5\s+RiX17"\s+data-text="(\d+)"[^>]*>', page_source)
        if reactions_match:
            post_data["likes"] = extract_count_from_string(reactions_match.group(1))
        else:
            reactions_text_match = re.search(r'<span\s+class="_3u7ZG\s+_UuSG\s+_3_54N\s+a8-QN\s+_2cSLK\s+L4pn5\s+RiX17"[^>]*>(\d+)\s*</span>', page_source)
            if reactions_text_match:
                post_data["likes"] = extract_count_from_string(reactions_text_match.group(1))
            else:
                post_data["likes"] = 0
        print(f"    Reactions (Likes): {post_data['likes']}")

        # Reposts
        reposts_match = re.search(r'(\d[\d,.]*[KMBTkmb]?)\s*Reposts', page_source, re.IGNORECASE)
        post_data["reposts_count"] = extract_count_from_string(reposts_match.group(1)) if reposts_match else 0
        print(f"    Reposts: {post_data['reposts_count']}")

        # Quotes
        quotes_match = re.search(r'(\d[\d,.]*[KMBTkmb]?)\s*Quotes', page_source, re.IGNORECASE)
        post_data["quotes_count"] = extract_count_from_string(quotes_match.group(1)) if quotes_match else 0
        print(f"    Quotes: {post_data['quotes_count']}")

        # Views
        views_button_match = re.search(r'<button[^>]*\s(?:title|aria-label)="(\d[\d,.]*[KMBTkmb]?)\s*views"[^>]*>', page_source, re.IGNORECASE)
        if views_button_match:
            post_data["views"] = extract_count_from_string(views_button_match.group(1))
        else:
            views_span_match = re.search(r'([\d,.]*[KMBTkmb]?)\s*<span[^>]*>views</span>', page_source, re.IGNORECASE)
            if views_span_match:
                post_data["views"] = extract_count_from_string(views_span_match.group(1))
            else:
                views_text_match = re.search(r'(\d[\d,.]*[KMBTkmb]?)\s*views', page_source, re.IGNORECASE)
                post_data["views"] = extract_count_from_string(views_text_match.group(1)) if views_text_match else 0
        print(f"    Views: {post_data['views']}")


    except TimeoutException as e:
        print(f"  Timeout waiting for a critical sub-element of the main post. Error: {e}. Skipping post.")
        return None
    except NoSuchElementException as e:
        print(f"  Could not find a critical sub-element within the main post on {post_url}. Error: {e}. Skipping post.")
        return None
    except StaleElementReferenceException:
        print(f"  Stale element encountered on {post_url} while scraping main post details. Skipping.")
        return None
    except Exception as e:
        print(f"  An unexpected general error occurred while scraping main post details for {post_url}: {e}. Skipping post.")
        return None

    # --- SCRAPE REPLIES SECTION ---
    print("\n  Attempting to scrape replies...")
    
    # Identify a scrollable container for replies, then scroll it
    comment_section_xpaths = [
        '//div[@id="comment-list"]',
        '//div[contains(@class, "post-replies-section") and contains(@class, "overflow-y-auto")]',
        '//section[contains(@aria-label, "Comments section")]',
        '//div[contains(@class, "comments-container") and contains(@class, "overflow-auto")]',
        '//div[contains(@id, "comments")]',
        '//div[contains(@class, "reply-list-container")]',
        '//div[contains(@class, "feed-comments")]',
        '//div[contains(@class, "comments-view")]',
        '//div[contains(@class, "post-replies")]',
        '//div[contains(@class, "max-h-96") and contains(@class, "overflow-auto")]',
        '//div[contains(@class, "overflow-y-scroll") and contains(@class, "flex-grow")]'
    ]
    
    scrolled_comments = False
    for xpath in comment_section_xpaths:
        if scroll_element_to_end(driver, xpath, scroll_pause_time=2, max_scrolls=7):
            scrolled_comments = True
            break
    
    if not scrolled_comments:
        print("  Could not find or scroll a dedicated comments container. Proceeding with visible replies.")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)


    try:
        time.sleep(2) 

        reply_element_xpaths = [
            '//div[starts-with(@data-comment, "")]',
            '//div[contains(@class, "_2hI-g") and contains(@class, "_3d6X0") and contains(@class, "_1qZTN") and contains(@class, "_36P3U") and starts-with(@data-comment, "")]'
        ]

        WebDriverWait(driver, 15).until(
            any_of_conditions(*[EC.presence_of_element_located((By.XPATH, xp)) for xp in reply_element_xpaths])
        )
        
        all_reply_elements = []
        for xp in reply_element_xpaths:
            all_reply_elements.extend(driver.find_elements(By.XPATH, xp))
        
        unique_reply_elements = []
        seen_identifiers = set()

        for elem in all_reply_elements:
            try:
                elem_identifier = elem.get_attribute('data-comment')
                if elem_identifier and elem_identifier not in seen_identifiers:
                    unique_reply_elements.append(elem)
                    seen_identifiers.add(elem_identifier)
            except StaleElementReferenceException:
                print("  Stale element encountered during reply collection, skipping.")
                continue 

        print(f"  Found {len(unique_reply_elements)} unique reply elements for detailed scraping.")

        for i, reply_elem in enumerate(unique_reply_elements):
            reply_info = {}
            reply_info["reply_id"] = reply_elem.get_attribute('data-comment') or f"reply_{i}_{datetime.now().timestamp()}"

            # Scrape Reply Text
            reply_text = "No text found."
            try:
                text_elem = WebDriverWait(reply_elem, 3).until( 
                    EC.presence_of_element_located((By.XPATH, './/div[@tabindex="0"]/p'))
                )
                raw_reply_text = text_elem.text.strip()
                if len(raw_reply_text) > 0:
                    reply_text = raw_reply_text
                else:
                    reply_text = "No substantive text found."

            except (TimeoutException, NoSuchElementException):
                reply_text = "No text found (Timeout/Not Found)."
            except Exception as e:
                print(f"      Error retrieving reply {i} text: {e}")
                reply_text = "Error retrieving text."
            reply_info["reply_text"] = reply_text

            # Scrape Reply Timestamp
            reply_info["reply_timestamp_raw"] = "N/A"
            reply_info["reply_age_text"] = "N/A"
            reply_info["reply_timestamp_iso"] = "N/A"
            try:
                timestamp_elem = WebDriverWait(reply_elem, 3).until( 
                    EC.presence_of_element_located((By.XPATH, './/a[contains(@href, "/posts/")]/span/time'))
                )
                reply_info["reply_timestamp_raw"] = timestamp_elem.get_attribute('datetime')
                reply_info["reply_age_text"] = timestamp_elem.text.strip()
                
                reply_info["reply_timestamp_iso"] = parse_gab_datetime(reply_info["reply_timestamp_raw"])

            except (TimeoutException, NoSuchElementException):
                print(f"      Warning: Reply {i} timestamp element timed out or not found.")
            except Exception as e:
                print(f"      Error scraping reply {i} timestamp: {e}")
            
            # Add: Scrape Image URLs for replies
            reply_info["image_urls"] = []
            try:
                reply_html = reply_elem.get_attribute('outerHTML')
                img_src_pattern = re.compile(r'src="(https?://m3\.gab\.com/media_attachments/[^"]+\.(?:jpg|jpeg|png|gif|webp)[^"]*)"')
                
                for match in img_src_pattern.finditer(reply_html):
                    reply_info["image_urls"].append(match.group(1))
                reply_info["image_urls"] = list(set(reply_info["image_urls"])) # Remove duplicates
                if reply_info["image_urls"]:
                    print(f"      Found {len(reply_info['image_urls'])} image URLs for reply {i+1}.")
            except Exception as e:
                print(f"      Error scraping image URLs for reply {i}: {e}")

            post_data["replies"].append(reply_info)
            print(f"    Scraped Reply {i+1}: Text='{reply_info['reply_text'][:50]}...', Age='{reply_info['reply_age_text']}', Image URLs={len(reply_info['image_urls'])}")

    except TimeoutException:
        print("  No replies found or timed out waiting for replies on this post (after potential scrolling).")
    except StaleElementReferenceException:
        print("  Stale element encountered during reply scraping main loop. Some replies might be missed. Retrying...")
    except Exception as e:
        print(f"  An error occurred while trying to find or process reply elements: {e}")

    # Reply count filter
    if len(post_data["replies"]) < MIN_REPLIES_REQUIRED:
        print(f"  Post only has {len(post_data['replies'])} replies, which is less than the required {MIN_REPLIES_REQUIRED}. Skipping post.")
        return None

    return post_data


def main():
    dataset = []  # ← always in scope
    dump_filename = None


    """Main function to orchestrate the scraping process from a predefined list of URLs."""
    print("Initializing WebDriver...")
    chrome_options = Options()
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")

    service = ChromeService(executable_path=ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        # === MANUAL LOGIN STEP ===
        print("\n" + "="*60)
        print("ACTION REQUIRED: Please log in to your Gab account in the browser window.")
        print("The script will wait for 90 seconds to allow you to complete the login.")
        print("DO NOT close the browser window.")
        print("="*60 + "\n")
        driver.get("https://gab.com/auth/sign_in")
        time.sleep(90) # Give ample time for manual login

        # Define your list of Gab post URLs here
        with open("gab_urls_4.json", "r", encoding="utf-8") as f:
            gab_urls = json.load(f)
        
        # Store the handle of the initial (main) window
        main_window_handle = driver.current_window_handle

        for url in gab_urls:
            print(f"\n--- Attempting to scrape: {url} ---")
            
            # Get current window handles before opening a new tab
            current_handles = driver.window_handles
            
            # Open a new tab
            driver.execute_script(f"window.open('{url}', '_blank');")

            # Wait for a new window handle to appear
            try:
                WebDriverWait(driver, 20).until(lambda d: len(d.window_handles) > len(current_handles))
                # Find the new window handle
                new_window_handle = [handle for handle in driver.window_handles if handle not in current_handles][0]
                driver.switch_to.window(new_window_handle)
                print(f"  Switched to new tab for {url}.")
            except TimeoutException:
                print(f"  Timed out waiting for new window for {url}. This URL will be skipped.")
                # If a new window didn't open or wasn't detected, ensure we're back to the main window
                driver.switch_to.window(main_window_handle)
                continue # Skip this URL and move to the next

            post_details = get_post_details(url, driver)

            # Close the current tab (the one just scraped)
            driver.close()
            # Switch back to the main window handle
            driver.switch_to.window(main_window_handle)
            
            if post_details:
                dataset.append(post_details)
                print(f"  Successfully scraped post from {url}. Total valid scraped: {len(dataset)}")
                print(f"  Posts scraped so far: {len(dataset)}")
            else:
                print(f"  Post {url} did not meet criteria or failed to scrape. Skipping.")
            
            time.sleep(1) # Short pause between posts

        driver.quit()

        print("\n--- Scraping Complete ---")
        if dataset:
            print(f"Successfully scraped {len(dataset)} posts that meet all criteria.")
            output_filename = f"gab_data_from_list_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(dataset, f, indent=4, ensure_ascii=False)
            print(f"Data saved to {output_filename}")
        else:
            print("No posts found that meet the specified criteria within the provided list of URLs.")
    
    except KeyboardInterrupt:
        print("\n⚡️ Scrape interrupted by user. Dumping partial data…")
        # always quit the browser
        try: driver.quit()
        except: pass

        dump_filename = f"gab_data_interrupt_dump_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        with open(dump_filename, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, indent=4, ensure_ascii=False)
        print(f"Partial data saved to {dump_filename}")
        sys.exit(0)

    except Exception as e:
        print(f"\n!!! Unexpected error: {e}")
        try: driver.quit()
        except: pass
        dump_filename = f"gab_data_error_dump_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        with open(dump_filename, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, indent=4, ensure_ascii=False)
        print(f"Partial data has been dumped to {dump_filename}")
        sys.exit(1)

    finally:
        # on a clean run without interrupts/errors, ensure browser is closed
        try: driver.quit()
        except: pass

if __name__ == "__main__":
    main()