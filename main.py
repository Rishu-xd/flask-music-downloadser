from flask import Flask, jsonify, send_file, render_template, request
from playwright.sync_api import sync_playwright
import requests
import time
import os
from bs4 import BeautifulSoup
import telebot
import threading

# Initialize Flask app with the correct template folder
app = Flask(__name__, template_folder='templates')

# **Security Improvement**: Load sensitive information from environment variables
TELEGRAM_API_TOKEN = os.getenv('TELEGRAM_API_TOKEN', 'YOUR_TELEGRAM_API_TOKEN')
CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID', 'YOUR_TELEGRAM_CHANNEL_ID')  # e.g., '-1001882399396'
bot = telebot.TeleBot(TELEGRAM_API_TOKEN)

def download_video(url, save_path):
    """Download the MP4 file from the extracted URL."""
    response = requests.get(url, stream=True)
    with open(save_path, "wb") as file:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                file.write(chunk)
    return save_path

def download_and_send_to_telegram(url, filename, title):
    """Download the song and send it to the Telegram channel with a caption."""
    # Download the song
    download_video(url, filename)
    
    # Send the file to the Telegram channel with the title as caption
    with open(filename, 'rb') as audio_file:
        bot.send_audio(CHANNEL_ID, audio_file, caption=title)

def safe_filename(title):
    """Sanitize the song title to create a safe filename."""
    return "".join([c if c.isalnum() else "_" for c in title])[:50]

@app.route('/download_and_post', methods=['GET'])
def download_and_post():
    """Endpoint to download a song and post it to Telegram."""
    try:
        # Retrieve 'url' and 'title' from query parameters
        url = request.args.get('url')
        title = request.args.get('title', 'New Song')  # Default caption if title is missing

        if not url:
            return jsonify({"error": "URL parameter is missing"}), 400

        # Get the MP4 URL
        mp4_url = get_mp4_url(url)
        if not mp4_url:
            return jsonify({"error": "MP4 URL not found"}), 404

        # Create downloads directory if it doesn't exist
        if not os.path.exists('downloads'):
            os.makedirs('downloads')

        # Generate a sanitized filename with timestamp
        safe_title_str = safe_filename(title)
        filename = f"downloads/{safe_title_str}_{int(time.time())}.mp4"

        # Start the download and upload in a separate thread to prevent blocking
        thread = threading.Thread(target=download_and_send_to_telegram, args=(mp4_url, filename, title))
        thread.start()

        return jsonify({"success": f"Song '{title}' is being processed and will be posted to Telegram."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def search_songs(query):
    """Search for songs on JioSaavn using Playwright."""
    base_url = "https://www.jiosaavn.com"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Format the search URL
        search_url = f"{base_url}/search/song/{query}"
        page.goto(search_url, wait_until="networkidle")

        songs = []
        # Loop through first 20 list items
        for i in range(1, 21):
            title_xpath = f'//*[@id="root"]/div[2]/div[1]/div/main/div/div/section/ol/li[{i}]/div/article/div[2]/figure/figcaption/h4/a'
            artist_xpath = f'//*[@id="root"]/div[2]/div[1]/div/main/div/div/section/ol/li[{i}]/div/article/div[2]/figure/figcaption/p[1]/a'
            parent_div_xpath = f'//*[@id="root"]/div[2]/div[1]/div/main/div/div/section/ol/li[{i}]/div/article/div[2]/figure/div'
            
            # Find title element
            title_element = page.locator(title_xpath)

            if title_element.count() > 0:  # Ensure element exists
                title = title_element.inner_text()
                relative_url = title_element.get_attribute("href")
                full_url = f"{base_url}{relative_url}"  # Construct full URL
                
                # Find all artist elements
                artist_elements = page.locator(artist_xpath)
                artists = [artist_elements.nth(j).inner_text() for j in range(artist_elements.count())]
                artist_names = ", ".join(artists) if artists else "Unknown"

                # Locate the parent div and find the img tag within it
                parent_div = page.locator(parent_div_xpath)
                image_url = None
                if parent_div.count() > 0:
                    image_url = page.evaluate('(div) => { const img = div.querySelector("img"); return img ? img.src || img.getAttribute("data-src") : null; }', parent_div.element_handle())

                # Create song dictionary
                songs.append({
                    'title': title,
                    'artist': artist_names,
                    'url': full_url,
                    'image': image_url
                })
        
        browser.close()
        return songs

def get_mp4_url(url):
    """Extract MP4 URL from the network requests after playing the video."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        mp4_url = None

        def intercept_response(response):
            nonlocal mp4_url
            if ".mp4" in response.url:
                mp4_url = response.url

        page.on("response", intercept_response)
        page.goto(url)
        page.wait_for_timeout(3000)

        try:
            play_button = page.wait_for_selector('//*[@id="root"]/div[2]/div[1]/div/main/div[2]/figure/figcaption/div/p[1]/a', timeout=5000)
            play_button.click()
        except:
            pass

        page.wait_for_timeout(5000)
        browser.close()
        return mp4_url

@app.route('/')
def home():
    """Home page with search form."""
    return render_template('index.html')

@app.route('/search', methods=['GET', 'POST'])
def search():
    """Handle both search form submission and API requests."""
    query = request.args.get('query') or request.form.get('query')
    if not query:
        return render_template('index.html', error="Please enter a search query")
    
    # Retrieve 'page' parameter from query string, default to 1
    page = request.args.get('page', 1, type=int)
    page_size = 20

    # Fetch all search results
    results = search_songs(query)
    total_results = len(results)
    total_pages = (total_results -1) // page_size +1 if total_results > 0 else 1

    # Paginate results
    start = (page -1)*page_size
    end = start + page_size
    page_songs = results[start:end]

    return render_template('index.html', results=page_songs, query=query, page_number=page, total_pages=total_pages)

@app.route('/download/<path:url>')
def download_endpoint(url):
    """Endpoint to download a song."""
    try:
        # Get the MP4 URL
        mp4_url = get_mp4_url(url)
        if not mp4_url:
            return jsonify({"error": "MP4 URL not found"}), 404

        # Create downloads directory if it doesn't exist
        if not os.path.exists('downloads'):
            os.makedirs('downloads')

        # Generate a filename based on timestamp
        filename = f"downloads/song_{int(time.time())}.mp4"
        
        # Download the file
        download_video(mp4_url, filename)
        
        # Send the file to the user
        return send_file(filename, as_attachment=True)
    except Exception as e:
        return render_template('index.html', error=str(e))

if __name__ == '__main__':
    app.run(debug=True)
