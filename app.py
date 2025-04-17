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
DELAY = 3.0
TEMP_FOLDER = "temp_results"

app = Flask(__name__)
CORS(app)

if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

def init_driver():
    """Initialize Chrome with Render-specific settings"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920x1080")
    
    # Critical for Render
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # Anti-bot measures
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver

@app.route('/scrape', methods=['POST'])
def handle_scrape():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if not file.filename.lower().endswith('.csv'):
        return jsonify({"error": "Only CSV files supported"}), 400
    
    try:
        # Read CSV
        content = file.read().decode('utf-8').splitlines()
        reader = csv.DictReader(content)
        mc_numbers = [row['MC_NUMBER'].strip() for row in reader if row.get('MC_NUMBER')]
        
        if not mc_numbers:
            return jsonify({"error": "No valid MC numbers found"}), 400
        
        # Setup Excel
        result_file = f"{TEMP_FOLDER}/{uuid.uuid4()}.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["MC Number", "Company Name", "Phone", "Address", "Status"])
        
        # Scrape data
        driver = init_driver()
        found = 0
        
        for mc in mc_numbers:
            try:
                driver.get("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx")
                
                # Search by MC number
                WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                    EC.element_to_be_clickable((By.XPATH, '//input[@value="MC"]'))).click()
                
                search_box = WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                    EC.presence_of_element_located((By.XPATH, '//input[@name="snapshot_id"]')))
                search_box.clear()
                search_box.send_keys(mc)
                
                WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                    EC.element_to_be_clickable((By.XPATH, '//input[@type="submit"]'))).click()
                
                # Extract data
                data = {
                    "MC Number": mc,
                    "Company Name": driver.find_element(
                        By.XPATH, '//td[contains(text(), "Legal Name")]/following-sibling::td').text,
                    "Phone": driver.find_element(
                        By.XPATH, '//td[contains(text(), "Phone")]/following-sibling::td').text,
                    "Address": driver.find_element(
                        By.XPATH, '//td[contains(text(), "Physical Address")]/following-sibling::td').text,
                    "Status": driver.find_element(
                        By.XPATH, '//td[contains(text(), "Operating Status")]/following-sibling::td').text
                }
                
                if "AUTHORIZED" in data["Status"].upper():
                    sheet.append(list(data.values()))
                    found += 1
                
            except Exception as e:
                print(f"Error processing MC {mc}: {str(e)}")
            finally:
                time.sleep(DELAY)
        
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
    try:
        return send_file(
            f"{TEMP_FOLDER}/{filename}",
            as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
