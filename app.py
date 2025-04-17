import csv
import time
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from openpyxl import Workbook
import os
import threading
import uuid
import atexit
import shutil

# Configuration
os.environ['WDM_LOG_LEVEL'] = '0'
os.environ['WDM_LOCAL'] = '1'
MAX_THREADS = 3  # Reduced for stability
PAGE_LOAD_TIMEOUT = 25
ELEMENT_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 1.0
TEMP_FOLDER = "temp_results"

app = Flask(__name__)
CORS(app)

if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

def cleanup_temp_files():
    now = time.time()
    for f in os.listdir(TEMP_FOLDER):
        file_path = os.path.join(TEMP_FOLDER, f)
        if os.stat(file_path).st_mtime < now - 24 * 3600:
            if os.path.isfile(file_path):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)

atexit.register(cleanup_temp_files)

def init_driver():
    """Initialize Chrome with robust error handling"""
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        return driver
    except Exception as e:
        print(f"🔥 Failed to initialize driver: {str(e)}")
        raise

def get_element_text(driver, xpath, default="NOT FOUND"):
    """Robust element text extraction"""
    try:
        element = WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, xpath)))
        return element.text.strip().replace('&nbsp;', '').replace('\n', ' ') or default
    except Exception as e:
        print(f"⚠️ Element not found: {xpath} - {str(e)}")
        return default

def process_mc_numbers(mc_numbers, job_id):
    """Fixed processing with proper error handling"""
    result_file = os.path.join(TEMP_FOLDER, f"{job_id}.xlsx")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["MC Number", "Company Name", "Phone", "Physical Address", "Status"])
    total_found = 0

    for mc in mc_numbers:
        driver = None
        try:
            driver = init_driver()
            print(f"🔍 Processing MC: {mc}")
            
            # Step 1: Load page
            driver.get("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx")
            
            # Step 2: Select search type
            WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="2"]'))).click()
            
            # Step 3: Enter MC number
            search_box = WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="4"]')))
            search_box.clear()
            search_box.send_keys(mc)
            
            # Step 4: Submit search
            WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, '/html/body/form/p/table/tbody/tr[4]/td/input'))).click()

            # Check for errors
            if "Record Not Found" in driver.page_source:
                print(f"❌ MC {mc} not found")
                continue
                
            # Verify authorization
            status = get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[8]/td')
            if "AUTHORIZED FOR Property" not in status:
                print(f"⛔ MC {mc} not authorized")
                continue

            # Extract data
            result = [
                mc,
                get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[11]/td'),
                get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[14]/td'),
                get_element_text(driver, '//*[@id="physicaladdressvalue"]'),
                status
            ]
            sheet.append(result)
            total_found += 1
            print(f"✅ Found data for MC {mc}")

        except Exception as e:
            print(f"🔥 Error processing MC {mc}: {str(e)}")
        finally:
            if driver:
                driver.quit()
            time.sleep(DELAY_BETWEEN_REQUESTS)

    workbook.save(result_file)
    print(f"🎉 Saved {total_found} records to {result_file}")
    return {"total_found": total_found, "result_file": result_file}

@app.route('/api/scrape', methods=['POST'])
def start_scraping():
    """API endpoint to start scraping process"""
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400
    
    file = request.files['file']
    if not file.filename.lower().endswith('.csv'):
        return jsonify({"status": "error", "message": "Only CSV files are supported"}), 400
    
    # Save the file temporarily
    temp_csv = os.path.join(TEMP_FOLDER, f"upload_{uuid.uuid4().hex}.csv")
    file.save(temp_csv)
    
    try:
        with open(temp_csv, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if 'MC_NUMBER' not in reader.fieldnames:
                return jsonify({"status": "error", "message": "CSV must have 'MC_NUMBER' column"}), 400
            
            mc_numbers = [row['MC_NUMBER'].strip() for row in reader if row['MC_NUMBER'].strip()]
            
        if not mc_numbers:
            return jsonify({"status": "error", "message": "No valid MC numbers found"}), 400
        
        job_id = uuid.uuid4().hex
        threading.Thread(
            target=process_mc_numbers,
            args=(mc_numbers, job_id),
            daemon=True
        ).start()
        
        return jsonify({
            "status": "success",
            "job_id": job_id,
            "message": f"Processing {len(mc_numbers)} MC numbers"
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        if os.path.exists(temp_csv):
            os.remove(temp_csv)

@app.route('/api/status/<job_id>', methods=['GET'])
def check_status(job_id):
    """Check status of a scraping job"""
    result_file = os.path.join(TEMP_FOLDER, f"{job_id}.xlsx")
    if os.path.exists(result_file):
        return jsonify({
            "status": "completed",
            "message": "Job completed successfully",
            "download_url": f"/api/download/{job_id}"
        })
    else:
        return jsonify({
            "status": "processing",
            "message": "Job is still in progress"
        })

@app.route('/api/download/<job_id>', methods=['GET'])
def download_results(job_id):
    """Download the results Excel file"""
    result_file = os.path.join(TEMP_FOLDER, f"{job_id}.xlsx")
    if not os.path.exists(result_file):
        return jsonify({"status": "error", "message": "Result file not found"}), 404
    
    return send_file(
        result_file,
        as_attachment=True,
        download_name=f"fmcsa_results_{job_id}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
