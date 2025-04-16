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
import queue
from datetime import datetime, timedelta
import uuid
import atexit
import shutil

# Configuration
os.environ['WDM_LOG_LEVEL'] = '0'  # Disable webdriver-manager logs
os.environ['WDM_LOCAL'] = '1'      # Use local chromedriver
MAX_THREADS = 5                    # Reduced for Render's free tier
PAGE_LOAD_TIMEOUT = 20
ELEMENT_TIMEOUT = 10
DELAY_BETWEEN_REQUESTS = 0.5       # Increased delay for stability
TEMP_FOLDER = "temp_results"

app = Flask(__name__)
CORS(app)

# Create temp folder if not exists
if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

# Cleanup function for temp files
def cleanup_temp_files():
    now = time.time()
    for f in os.listdir(TEMP_FOLDER):
        file_path = os.path.join(TEMP_FOLDER, f)
        if os.stat(file_path).st_mtime < now - 24 * 3600:  # Delete files older than 24h
            if os.path.isfile(file_path):
                os.remove(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)

atexit.register(cleanup_temp_files)

# Fixed Chrome Driver Initialization
def init_driver():
    """Initialize a Chrome driver instance compatible with Render"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Fixed ChromeDriver installation for Render
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver

def get_element_text(driver, xpath, default="NOT FOUND"):
    """Quick element text extraction with timeout"""
    try:
        element = WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, xpath)))
        return element.text.strip().replace('&nbsp;', '').replace('\n', ' ') or default
    except:
        return default

def process_mc_numbers(mc_numbers, job_id):
    print(f"🚀 Starting job {job_id} with {len(mc_numbers)} MC numbers")
    result_file = os.path.join(TEMP_FOLDER, f"{job_id}.xlsx")
    
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["MC Number", "Company Name", "Phone", "Physical Address", "Status"])
    
    total_found = 0
    
    for i, mc in enumerate(mc_numbers, 1):
        try:
            print(f"\n🔍 Processing MC #{i}: {mc}")
            driver = init_driver()
            
            # Debug: Print Chrome version
            print(f"🌐 Chrome version: {driver.capabilities['browserVersion']}")
            
            driver.get("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx")
            print("✅ Loaded homepage")
            
            # Step 1: Click search type
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="2"]'))).click()
            print("✅ Selected search type")
            
            # Step 2: Enter MC number
            search_box = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="4"]')))
            search_box.clear()
            search_box.send_keys(mc)
            print(f"✅ Entered MC number: {mc}")
            
            # Step 3: Click search
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, '/html/body/form/p/table/tbody/tr[4]/td/input'))).click()
            print("✅ Clicked search button")
            
            # Check for "Not Found"
            try:
                WebDriverWait(driver, 3).until(
                    EC.presence_of_element_located((By.XPATH, '//*[contains(text(), "Record Not Found")]')))
                print(f"❌ MC {mc} not found")
                continue
            except:
                pass
            
            # Check status
            status = get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[8]/td')
            print(f"📋 Status text: {status}")
            
            if "AUTHORIZED FOR Property" not in status:
                print(f"⛔ MC {mc} not authorized")
                continue
                
            # Extract data
            result = {
                "MC Number": mc,
                "Company Name": get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[11]/td'),
                "Phone": get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[14]/td'),
                "Physical Address": get_element_text(driver, '//*[@id="physicaladdressvalue"]'),
                "Status": status
            }
            print(f"✅ Found data: {result}")
            
            sheet.append(list(result.values()))
            total_found += 1
            
        except Exception as e:
            print(f"🔥 Error processing MC {mc}: {str(e)}")
        finally:
            driver.quit()
    
    workbook.save(result_file)
    print(f"\n🎉 Saved {total_found} records to {result_file}")
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
