import time
from datetime import datetime, timezone
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
POSTS_TO_SCRAPE = 10
SCROLL_PAUSE_TIME = 2
MAX_SCROLLS = 10

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
            num_str = match.group(1).replace(',', '').strip() # Remove commas and strip spaces
            
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
                return int(float(num_str)) # Convert to int, handling decimals if present (e.g., 1.0 becomes 1)
    except (ValueError, AttributeError):
        pass # Failed to convert, or attribute not found
    return 0


def scroll_to_end(driver, scroll_pause_time=SCROLL_PAUSE_TIME, max_scrolls=MAX_SCROLLS):
    """Scrolls down the page to load more content, for infinite scrolling feeds."""
    print(f"Initiating scroll sequence to load more posts (max {max_scrolls} scrolls)...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_count = 0
    while scroll_count < max_scrolls:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause_time)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            print(f"  Reached end of scrollable content or no new content loaded after {scroll_count + 1} scrolls.")
            break
        last_height = new_height
        scroll_count += 1
        print(f"  Scrolled {scroll_count} times. Page height: {new_height}px")
    print("Finished scrolling sequence.")

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

    post_data = {"post_id": post_id, "post_url": post_url, "image_urls": [], "replies": []} # Added post_url
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
                    EC.presence_of_element_located((By.XPATH, './/span[@data-text-content]')),
                    EC.presence_of_element_located((By.XPATH, './/div[@data-text-content]')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "break-words") and not(contains(@class, "post-header")) and not(contains(@class, "flex")) and not(contains(@class, "text-sm"))]')), # Exclude common header/meta classes
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-content-body")]')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-content")]')), 
                    EC.presence_of_element_located((By.XPATH, './/p[string-length(normalize-space(.)) > 5 and not(ancestor::div[contains(@class, "post-header")])]')), # Require some actual text (min 5 chars), exclude if part of header
                    EC.presence_of_element_located((By.XPATH, './/div[not(contains(@class, "hidden")) and string-length(normalize-space(.)) > 5 and not(contains(@class, "meta")) and not(contains(@class, "header")) and not(contains(@class, "text-gray"))]')), # More robust exclusion
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-description")]'))
                )
            )
            raw_text = text_element.text.strip()
            
            # More robust heuristic to filter out author/timestamp lines if mistaken for post text
            is_likely_header = False
            if post_data["author_username"] != "N/A":
                # Check if the "text" content contains the author's display name or handle at the start
                display_name_match = re.match(r'(.+?)\n@\w+', raw_text) 
                if display_name_match:
                    is_likely_header = True

            if len(raw_text) < 20 and ("·" in raw_text or "\n" in raw_text) and not re.search(r'[a-zA-Z]{5,}', raw_text): 
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
            # Get the outer HTML of the main post element to limit the search scope
            main_post_html = main_post_element.get_attribute('outerHTML')
            image_urls = []
            # Regex to capture a broader range of image paths and extensions
            img_src_pattern = re.compile(r'src="(https?://m3\.gab\.com/media_attachments/[^"]+\.(?:jpg|jpeg|png|gif|webp)[^"]*)"')
            
            for match in img_src_pattern.finditer(main_post_html):
                image_urls.append(match.group(1))
            
            post_data["image_urls"] = list(set(image_urls)) # Use set to remove duplicates
            print(f"    Found {len(post_data['image_urls'])} image URLs.")
        except Exception as e:
            post_data["image_urls"] = []
            print(f"    Error scraping image URLs from main post element: {e}")


        # Scrape timestamp
        try:
            print("    Attempting to scrape timestamp...")
            timestamp_elem = WebDriverWait(main_post_element, 10).until(
                any_of_conditions(
                    EC.presence_of_element_located((By.TAG_NAME, 'time')),
                    EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "post-meta")]//time')),
                    EC.presence_of_element_located((By.XPATH, './/a[contains(@href, "/posts/") and contains(@href, "/posts/")]//time')),
                    EC.presence_of_element_located((By.XPATH, './/span[contains(@class, "timestamp")]'))
                )
            )
            post_data["timestamp"] = timestamp_elem.get_attribute('datetime') or timestamp_elem.text.strip()
            print(f"    Timestamp: {post_data['timestamp']}")
        except TimeoutException:
            post_data["timestamp"] = "N/A (Timeout)"
            print("    Warning: Timestamp element timed out.")
        except NoSuchElementException:
            post_data["timestamp"] = "N/A"
            print("    Warning: Timestamp not found for main post.")
        except Exception as e:
            print(f"    Error scraping timestamp: {e}")
            post_data["timestamp"] = "Error"


        # Scrape reactions, reposts, quotes, and views from the entire page source using regex
        print("    Attempting to scrape interactions (reactions, reposts, quotes, views) from page source...")
        page_source = driver.page_source
        
        # Reactions (formerly 'likes')
        # Based on: <span class="_3u7ZG _UuSG _3_54N a8-QN _2cSLK L4pn5 RiX17" data-text="257">257</span>
        reactions_match = re.search(r'<span\s+class="_3u7ZG\s+_UuSG\s+_3_54N\s+a8-QN\s+_2cSLK\s+L4pn5\s+RiX17"\s+data-text="(\d+)"[^>]*>', page_source)
        if reactions_match:
            post_data["likes"] = extract_count_from_string(reactions_match.group(1)) # Use data-text
        else:
            # Fallback if data-text is not consistently present or structure changes slightly
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
        # New strategy: Prioritize title/arialabel attributes of the button containing view count
        views_button_match = re.search(r'<button[^>]*\s(?:title|aria-label)="(\d[\d,.]*[KMBTkmb]?)\s*views"[^>]*>', page_source, re.IGNORECASE)
        if views_button_match:
            post_data["views"] = extract_count_from_string(views_button_match.group(1))
        else:
            # Fallback to the span text if button attributes are not found
            views_span_match = re.search(r'([\d,.]*[KMBTkmb]?)\s*&nbsp;views', page_source, re.IGNORECASE)
            post_data["views"] = extract_count_from_string(views_span_match.group(1)) if views_span_match else 0
        print(f"    Views: {post_data['views']}")


    except TimeoutException as e:
        print(f"  Timeout waiting for a critical sub-element of the main post. Error: {e}")
        return None
    except NoSuchElementException as e:
        print(f"  Could not find a critical sub-element within the main post on {post_url}. Error: {e}")
        return None
    except StaleElementReferenceException:
        print(f"  Stale element encountered on {post_url} while scraping main post details. Skipping.")
        return None
    except Exception as e:
        print(f"  An unexpected general error occurred while scraping main post details for {post_url}: {e}")
        return None

    # Scrape replies (unchanged for now, assuming previous logic was fine)
    print("  Attempting to scrape replies...")
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, 
                '//div[contains(@data-comment, "")] | '
                '//div[contains(@class, "reply-item")] | '
                '//div[contains(@class, "comment-wrapper")]'
            ))
        )
        reply_elements = driver.find_elements(By.XPATH, 
            '//div[contains(@data-comment, "")] | '
            '//div[contains(@class, "reply-item")] | '
            '//div[contains(@class, "comment-wrapper")]'
        )
        
        filtered_replies = []
        for r_elem in reply_elements:
            try:
                potential_author_link = r_elem.find_elements(By.XPATH, './/a[contains(@href, "/profile/")]')
                potential_text_elem = r_elem.find_elements(By.XPATH, 
                    './/span[@data-text-content] | '
                    './/div[@data-text-content] | '
                    './/div[contains(@class, "break-words")] | '
                    './/p[contains(@class, "comment-content")] | '
                    './/div[contains(@class, "comment-text")] | '
                    './/p[string-length(normalize-space(.)) > 1]'
                )
                
                if potential_author_link and potential_text_elem:
                    filtered_replies.append(r_elem)
            except StaleElementReferenceException:
                print("    Stale element encountered during reply filtering. Skipping.")
                continue
            except Exception as e:
                print(f"    Error during reply element filtering: {e}")
                continue

        print(f"  Found {len(filtered_replies)} valid replies after filtering.")

        for i, reply in enumerate(filtered_replies):
            try:
                reply_author = "N/A"
                # For replies, we'll keep the direct scraping of author name as the URL approach might not be feasible for nested comments
                try:
                    reply_author_link = WebDriverWait(reply, 7).until(
                        EC.presence_of_element_located((By.XPATH, './/a[contains(@href, "/profile/")]'))
                    )
                    
                    potential_username = "N/A"
                    try:
                        reply_author_elem = WebDriverWait(reply_author_link, 3).until(
                            any_of_conditions(
                                EC.presence_of_element_located((By.XPATH, './/span[contains(@class, "_2mtbj")]/bdi/strong')), 
                                EC.presence_of_element_located((By.XPATH, './/span[contains(@class, "font-bold")]')),
                                EC.presence_of_element_located((By.XPATH, './/span[starts-with(@class, "username")]')),
                                EC.presence_of_element_located((By.XPATH, './/span[starts-with(@class, "flex")]/span[contains(@class, "font-bold")]')),
                                EC.presence_of_element_located((By.XPATH, './/span'))
                            )
                        )
                        potential_username = reply_author_elem.text.strip()
                        if potential_username.startswith('@'):
                            reply_author = potential_username
                        else: 
                            handle_elem = reply_author_link.find_elements(By.XPATH, './/span[contains(text(), "@")]')
                            if handle_elem:
                                reply_author = handle_elem[0].text.strip()
                                if not reply_author.startswith('@'): 
                                    reply_author = "@" + reply_author
                    except (TimeoutException, NoSuchElementException):
                        pass

                    if not reply_author or reply_author == "N/A" or not reply_author.startswith('@'):
                        link_text = reply_author_link.text.strip()
                        if link_text:
                            match = re.search(r'@(\w+)', link_text)
                            if match:
                                reply_author = "@" + match.group(1).strip()
                            elif link_text and not link_text.startswith('@'):
                                reply_author = "@" + link_text.replace(" ", "")

                    if not reply_author or reply_author == "N/A" or not reply_author.startswith('@'):
                        aria_label_author = reply_author_link.get_attribute('aria-label')
                        if aria_label_author:
                            match = re.search(r'@(\w+)', aria_label_author)
                            if match:
                                reply_author = "@" + match.group(1).strip()
                    
                    if reply_author and not reply_author.startswith('@') and reply_author != "N/A":
                        reply_author = "@" + reply_author.replace(" ", "")

                except (TimeoutException, NoSuchElementException):
                    print(f"      Warning: Reply {i} author element timed out or not found.")
                    reply_author = "N/A (Timeout/Not Found)"
                except Exception as e:
                    print(f"      Error scraping reply {i} author: {e}")
                    reply_author = "Error"

                reply_text = "No text found."
                try:
                    reply_text_elem = WebDriverWait(reply, 7).until(
                        any_of_conditions(
                            EC.presence_of_element_located((By.XPATH, './/span[@data-text-content]')),
                            EC.presence_of_element_located((By.XPATH, './/div[@data-text-content]')),
                            EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "break-words")]')),
                            EC.presence_of_element_located((By.XPATH, './/p[contains(@class, "comment-content")]')),
                            EC.presence_of_element_located((By.XPATH, './/div[contains(@class, "comment-text")]')),
                            EC.presence_of_element_located((By.XPATH, './/p[string-length(normalize-space(.)) > 1]'))
                        )
                    )
                    reply_text = reply_text_elem.text.strip()
                except (TimeoutException, NoSuchElementException):
                    print(f"      Warning: Reply {i} text element timed out or not found.")
                    reply_text = "No text found (Timeout/Not Found)."
                except Exception as e:
                    print(f"      Error retrieving reply {i} text: {e}")
                    reply_text = "Error retrieving text."

                reply_timestamp = "N/A"
                try:
                    reply_timestamp_elem = WebDriverWait(reply, 5).until(
                        any_of_conditions(
                            EC.presence_of_element_located((By.TAG_NAME, 'time')),
                            EC.presence_of_element_located((By.XPATH, './/span[contains(@class, "timestamp")]')),
                            EC.presence_of_element_located((By.XPATH, './/a[contains(@href, "/posts/")]//time'))
                        )
                    )
                    reply_timestamp = reply_timestamp_elem.get_attribute('datetime') or reply_timestamp_elem.text.strip()
                except (TimeoutException, NoSuchElementException):
                    print(f"      Warning: Reply {i} timestamp element timed out or not found.")
                    reply_timestamp = "N/A (Timeout/Not Found)"
                except Exception as e:
                    print(f"      Error scraping reply {i} timestamp: {e}")
                    reply_timestamp = "Error"
                
                reply_id = reply.get_attribute('data-comment') or reply.get_attribute('data-id')
                if not reply_id:
                    reply_id = f"reply_{i}_{abs(hash(reply_text + reply_author))}"

                reply_info = {
                    "reply_id": reply_id,
                    "reply_author": reply_author,
                    "reply_text": reply_text,
                    "reply_timestamp": reply_timestamp
                }
                
                reply_info["reply_likes"] = 0
                try:
                    # For replies, it's often a span with text containing the number.
                    reply_like_match = re.search(r'(\d[\d,.]*[KMBTkmb]?)\s*(?:Likes|Reactions)', reply.get_attribute('outerHTML'), re.IGNORECASE)
                    reply_info["reply_likes"] = extract_count_from_string(reply_like_match.group(1)) if reply_like_match else 0
                except Exception as e:
                    print(f"      Warning: Error scraping reply {i} likes: {e}")

                post_data["replies"].append(reply_info)
            except StaleElementReferenceException:
                print(f"    Stale element encountered for reply {i}. Skipping this reply.")
                continue
            except Exception as e:
                print(f"    An unexpected general error occurred while scraping reply {i}: {e}")
                continue
    except TimeoutException:
        print("  No replies found or timed out waiting for replies on this post.")
    except Exception as e:
        print(f"  An error occurred while trying to find reply elements: {e}")

    return post_data

def main():
    """Main function to orchestrate the scraping process."""
    print("Initializing WebDriver...")
    chrome_options = Options()
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")

    service = ChromeService(executable_path=ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    # === MANUAL LOGIN STEP ===
    print("\n" + "="*60)
    print("ACTION REQUIRED: Please log in to your Gab account in the browser window.")
    print("The script will wait for 90 seconds to allow you to complete the login.")
    print("DO NOT close the browser window.")
    print("="*60 + "\n")
    driver.get("https://gab.com/auth/sign_in")
    time.sleep(90) # Give ample time for manual login

    print("\nResuming script. Navigating to Gab Explore page...")
    driver.get("https://gab.com/explore")

    try:
        WebDriverWait(driver, 45).until(
            any_of_conditions(
                EC.presence_of_element_located((By.XPATH, '//main//a[contains(@href, "/posts/")]')),
                EC.presence_of_element_located((By.XPATH, '//div[contains(@class, "flex-1") and contains(@class, "items-center")]'))
            )
        )
        print("Initial content or post elements detected on Explore page.")
    except TimeoutException:
        print("ERROR: Timed out waiting for initial content on Explore page after login.")
        print("This could mean Gab's layout has changed, login failed, or there's a network issue.")
        driver.quit()
        return
    except Exception as e:
        print(f"An unexpected error occurred after navigating to Explore page: {e}")
        driver.quit()
        return

    scroll_to_end(driver)

    dataset = []
    print("\nCollecting post URLs from the Explore page...")
    
    unique_post_urls = []
    processed_urls = set()

    current_link_elements = driver.find_elements(By.XPATH, '//main//a[contains(@href, "/posts/")]')
    print(f"Found {len(current_link_elements)} raw potential post links on the page.")

    debug_counter = 0
    TARGET_SUBSTRING = "/posts/"

    i = 0
    while i < len(current_link_elements) and len(unique_post_urls) < POSTS_TO_SCRAPE:
        link_elem = current_link_elements[i]
        try:
            raw_url = link_elem.get_attribute('href')
            
            debug_counter += 1
            print(f"\n  Debug {debug_counter}: Raw URL from href: '{raw_url}'")

            url = urljoin(driver.current_url, raw_url)
            url = ''.join(c for c in url if not c.isspace()).strip()

            print(f"    Absolute URL after urljoin (normalized): '{url}'")

            contains_target_substring_in = TARGET_SUBSTRING in url

            if url and contains_target_substring_in and url.startswith("https://gab.com/"):
                post_id_candidate = url.split('/posts/')[-1].split('/')[0].split('#')[0].split('?')[0]
                
                if len(post_id_candidate) > 5 and post_id_candidate.isalnum() and url not in processed_urls:
                    unique_post_urls.append(url)
                    processed_urls.add(url)
                    print("    --> URL PASSED VALIDATION AND ADDED!") 
                else:
                    print("    --> URL FAILED VALIDATION or ALREADY PROCESSED.") 
            else:
                print("    --> URL failed general validation (does not contain '/posts/' or not from gab.com).")

        except StaleElementReferenceException:
            print("  Stale element encountered while collecting URLs. Re-collecting links and restarting iteration.")
            current_link_elements = driver.find_elements(By.XPATH, '//main//a[contains(@href, "/posts/")]')
            unique_post_urls.clear()
            processed_urls.clear()
            i = 0
            continue
        except Exception as e:
            print(f"  Error collecting URL from link element: {e}")
            pass
        i += 1

    print(f"\nFound {len(unique_post_urls)} unique post URLs to scrape after processing.")
    if not unique_post_urls:
        print("No unique post URLs found. Script will exit.")
        driver.quit()
        return

    for i, url in enumerate(unique_post_urls):
        if len(dataset) >= POSTS_TO_SCRAPE:
            print(f"Reached target of {POSTS_TO_SCRAPE} posts. Stopping.")
            break

        print(f"\n--- Scraping Post {i+1}/{len(unique_post_urls)}: {url} ---")
        
        driver.execute_script(f"window.open('{url}', '_blank');")
        driver.switch_to.window(driver.window_handles[-1])

        post_details = get_post_details(url, driver)

        driver.close()
        driver.switch_to.window(driver.window_handles[0])
        
        if post_details:
            dataset.append(post_details)
            print(f"  Successfully scraped post. Total scraped: {len(dataset)}/{POSTS_TO_SCRAPE}")
        else:
            print(f"  Failed to scrape details for {url}. Skipping.")
        
        time.sleep(1)

    driver.quit()

    print("\n--- Scraping Complete ---")
    if dataset:
        print(f"Successfully scraped {len(dataset)} posts.")
        output_filename = f"gab_data_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, indent=4, ensure_ascii=False)
        print(f"Data saved to {output_filename}")
    else:
        print("Could not find any posts to scrape or an error occurred during scraping.")

if __name__ == "__main__":
    main()