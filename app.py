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
import uuid

# Configuration
os.environ['WDM_LOG_LEVEL'] = '0'
os.environ['WDM_LOCAL'] = '1'
PAGE_LOAD_TIMEOUT = 30
ELEMENT_TIMEOUT = 15
DELAY = 3.0  # Critical for Render's free tier
TEMP_FOLDER = "temp_results"

app = Flask(__name__)
CORS(app)

if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

def init_driver():
    """Chrome setup optimized for Render"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920x1080")
    
    # Anti-detection measures
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    # Mask selenium
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver

def scrape_data(mc, driver):
    """Robust scraping function with current FMCSA selectors"""
    try:
        # 1. Load page
        driver.get("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx")
        
        # 2. Select MC search
        WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, '//input[@value="MC"]'))).click()
        
        # 3. Enter MC number
        search_box = WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, '//input[@name="snapshot_id"]')))
        search_box.clear()
        search_box.send_keys(mc)
        
        # 4. Submit search
        WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, '//input[@type="submit"]'))).click()
        
        # 5. Wait for results
        WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, '//h2[contains(text(), "Company Snapshot")]'))
        
        # 6. Extract data - Updated 2024 selectors
        return {
            "MC Number": mc,
            "Company Name": driver.find_element(By.XPATH, '//td[contains(text(), "Legal Name")]/following-sibling::td').text.strip(),
            "Phone": driver.find_element(By.XPATH, '//td[contains(text(), "Phone")]/following-sibling::td').text.strip(),
            "Address": driver.find_element(By.XPATH, '//td[contains(text(), "Physical Address")]/following-sibling::td').text.strip(),
            "Status": driver.find_element(By.XPATH, '//td[contains(text(), "Operating Status")]/following-sibling::td').text.strip()
        }
        
    except Exception as e:
        print(f"Error scraping {mc}: {str(e)}")
        return None

@app.route('/scrape', methods=['POST'])
def handle_scrape():
    """Fixed API endpoint"""
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if not file.filename.lower().endswith('.csv'):
        return jsonify({"error": "Only CSV files supported"}), 400
    
    try:
        # Read CSV
        mc_numbers = []
        reader = csv.DictReader(file.read().decode('utf-8').splitlines())
        mc_numbers = [row['MC_NUMBER'].strip() for row in reader if row.get('MC_NUMBER')]
        
        if not mc_numbers:
            return jsonify({"error": "No valid MC numbers"}), 400
        
        # Setup Excel
        result_file = f"{TEMP_FOLDER}/{uuid.uuid4()}.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["MC Number", "Company Name", "Phone", "Address", "Status"])
        
        # Scrape with single driver
        driver = init_driver()
        found = 0
        
        for mc in mc_numbers:
            data = scrape_data(mc, driver)
            if data and "AUTHORIZED" in data["Status"].upper():
                sheet.append([data["MC Number"], data["Company Name"], data["Phone"], 
                            data["Address"], data["Status"]])
                found += 1
            time.sleep(DELAY)  # Critical delay
            
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
def handle_download(filename):
    """Download endpoint"""
    try:
        return send_file(
            f"{TEMP_FOLDER}/{filename}",
            as_attachment=True,
            download_name="fmcsa_results.xlsx",
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
