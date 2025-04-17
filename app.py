import csv
import time
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from openpyxl import Workbook
import os
import threading
import uuid

# Configuration
os.environ['WDM_LOG_LEVEL'] = '0'  # Disable logs
os.environ['WDM_LOCAL'] = '1'      # Use local chromedriver
PAGE_LOAD_TIMEOUT = 25
ELEMENT_TIMEOUT = 15
DELAY = 2.0  # Increased delay for Render's free tier
TEMP_FOLDER = "temp_results"

app = Flask(__name__)
CORS(app)

if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

def init_driver():
    """Chrome setup for Render"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920x1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Anti-bot measures
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options
    )
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver

def scrape_mc(mc, driver):
    """Fixed scraping logic for current FMCSA site"""
    try:
        # 1. Load page
        driver.get("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx")
        
        # 2. Select MC search
        WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, '//input[@value="MC"]'))).click()
        
        # 3. Enter number
        search_box = WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, '//input[@name="snapshot_id"]')))
        search_box.clear()
        search_box.send_keys(mc)
        
        # 4. Submit
        WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, '//input[@type="submit"]'))).click()
        
        # 5. Verify results
        WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, '//h2[contains(., "Company Snapshot")]'))
        
        # 6. Extract data
        return {
            "MC Number": mc,
            "Company Name": driver.find_element(By.XPATH, '//td[contains(., "Legal Name")]/following-sibling::td').text,
            "Phone": driver.find_element(By.XPATH, '//td[contains(., "Phone")]/following-sibling::td').text,
            "Address": driver.find_element(By.XPATH, '//td[contains(., "Physical Address")]/following-sibling::td').text,
            "Status": driver.find_element(By.XPATH, '//td[contains(., "Operating Status")]/following-sibling::td').text
        }
        
    except Exception as e:
        print(f"Error scraping {mc}: {str(e)}")
        return None

@app.route('/scrape', methods=['POST'])
def start_scrape():
    """API endpoint with Render fixes"""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if not file.filename.lower().endswith('.csv'):
        return jsonify({"error": "Only CSV files supported"}), 400
    
    # Process file
    try:
        mc_numbers = []
        reader = csv.DictReader(file.read().decode('utf-8').splitlines())
        mc_numbers = [row['MC_NUMBER'].strip() for row in reader if row.get('MC_NUMBER')]
        
        if not mc_numbers:
            return jsonify({"error": "No valid MC numbers found"}), 400
        
        # Start scraping
        result_file = f"{TEMP_FOLDER}/{uuid.uuid4()}.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["MC Number", "Company Name", "Phone", "Address", "Status"])
        
        driver = init_driver()
        found = 0
        
        for mc in mc_numbers:
            data = scrape_mc(mc, driver)
            if data and "AUTHORIZED" in data["Status"].upper():
                sheet.append(list(data.values()))
                found += 1
            time.sleep(DELAY)  # Critical for Render
        
        driver.quit()
        workbook.save(result_file)
        
        return jsonify({
            "success": True,
            "found": found,
            "download_url": f"/download/{os.path.basename(result_file)}"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/<filename>', methods=['GET'])
def download(filename):
    """File download endpoint"""
    return send_file(
        f"{TEMP_FOLDER}/{filename}",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
