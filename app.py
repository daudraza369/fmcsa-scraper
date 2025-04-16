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
MAX_THREADS = 10
PAGE_LOAD_TIMEOUT = 15
ELEMENT_TIMEOUT = 5
DELAY_BETWEEN_REQUESTS = 0.15
TEMP_FOLDER = "temp_results"
MAX_REQUESTS_PER_HOUR = 100  # Rate limiting
CLEANUP_INTERVAL = 3600  # Clean temp files every hour (seconds)

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

# Register cleanup at exit
atexit.register(cleanup_temp_files)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Rate limiting storage
request_counts = {}

# Initialize drivers pool
driver_pool = queue.Queue(maxsize=MAX_THREADS)

def init_driver_pool():
    """Initialize a pool of Chrome drivers"""
    for _ in range(MAX_THREADS):
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options
        )
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver_pool.put(driver)

init_driver_pool()

def get_driver():
    """Get a driver from the pool"""
    return driver_pool.get()

def release_driver(driver):
    """Release a driver back to the pool"""
    driver_pool.put(driver)

def get_element_text(driver, xpath, default="NOT FOUND"):
    """Quick element text extraction with timeout"""
    try:
        element = WebDriverWait(driver, ELEMENT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, xpath)))
        return element.text.strip().replace('&nbsp;', '').replace('\n', ' ') or default
    except:
        return default

def process_mc_numbers(mc_numbers, job_id):
    """Process a list of MC numbers and save results to Excel"""
    result_file = os.path.join(TEMP_FOLDER, f"{job_id}.xlsx")
    
    # Create Excel file
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "FMCSA Authorized Carriers"
    headers = ["MC Number", "Company Name", "Phone Number", "Physical Address", "Status"]
    sheet.append(headers)
    
    # Set column widths
    sheet.column_dimensions['A'].width = 15
    sheet.column_dimensions['B'].width = 30
    sheet.column_dimensions['C'].width = 20
    sheet.column_dimensions['D'].width = 50
    sheet.column_dimensions['E'].width = 25
    
    total_found = 0
    processed = 0
    
    for mc in mc_numbers:
        driver = get_driver()
        try:
            # Open website and search
            driver.get("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx")
            WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="2"]'))).click()
            
            search_box = WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="4"]')))
            search_box.clear()
            search_box.send_keys(mc)
            
            WebDriverWait(driver, ELEMENT_TIMEOUT).until(
                EC.presence_of_element_located((By.XPATH, '/html/body/form/p/table/tbody/tr[4]/td/input'))).click()
            
            # Skip if not found or not authorized
            try:
                WebDriverWait(driver, 2).until(
                    EC.presence_of_element_located((By.XPATH, '//*[contains(text(), "Record Not Found")]')))
                processed += 1
                continue
            except:
                pass
                
            status = get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[8]/td')
            if "AUTHORIZED FOR Property" not in status:
                processed += 1
                continue
                
            # Collect authorized carrier data
            result = {
                "MC Number": mc,
                "Company Name": get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[11]/td'),
                "Phone": get_element_text(driver, '/html/body/p/table/tbody/tr[2]/td/table/tbody/tr[2]/td/center[1]/table/tbody/tr[14]/td'),
                "Physical Address": get_element_text(driver, '//*[@id="physicaladdressvalue"]'),
                "Status": "AUTHORIZED FOR Property"
            }
            
            sheet.append([
                result["MC Number"],
                result["Company Name"],
                result["Phone"],
                result["Physical Address"],
                result["Status"]
            ])
            
            total_found += 1
            processed += 1
            
        except Exception as e:
            print(f"Error processing MC {mc}: {str(e)}")
            processed += 1
        finally:
            release_driver(driver)
        
        time.sleep(DELAY_BETWEEN_REQUESTS)
    
    workbook.save(result_file)
    
    return {
        "total_processed": processed,
        "total_found": total_found,
        "result_file": result_file
    }

def check_rate_limit(ip):
    """Check if the IP has exceeded the rate limit"""
    now = time.time()
    if ip not in request_counts:
        request_counts[ip] = {"count": 1, "timestamp": now}
        return True
    
    # Reset count if last request was more than an hour ago
    if now - request_counts[ip]["timestamp"] > 3600:
        request_counts[ip] = {"count": 1, "timestamp": now}
        return True
    
    if request_counts[ip]["count"] >= MAX_REQUESTS_PER_HOUR:
        return False
    
    request_counts[ip]["count"] += 1
    return True

@app.route('/api/scrape', methods=['POST'])
def start_scraping():
    """API endpoint to start scraping process"""
    client_ip = request.remote_addr
    
    # Rate limiting check
    if not check_rate_limit(client_ip):
        return jsonify({
            "status": "error",
            "message": "Rate limit exceeded. Please try again later."
        }), 429
    
    # Check if file was uploaded
    if 'file' not in request.files:
        return jsonify({
            "status": "error",
            "message": "No file uploaded"
        }), 400
    
    file = request.files['file']
    
    # Check if file is CSV
    if not file.filename.lower().endswith('.csv'):
        return jsonify({
            "status": "error",
            "message": "Only CSV files are supported"
        }), 400
    
    # Save the file temporarily
    temp_csv = os.path.join(TEMP_FOLDER, f"upload_{uuid.uuid4().hex}.csv")
    file.save(temp_csv)
    
    # Read MC numbers from CSV
    try:
        with open(temp_csv, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if 'MC_NUMBER' not in reader.fieldnames:
                os.remove(temp_csv)
                return jsonify({
                    "status": "error",
                    "message": "CSV must have 'MC_NUMBER' column header"
                }), 400
            
            mc_numbers = [row['MC_NUMBER'].strip() for row in reader if row['MC_NUMBER'].strip()]
            
        if not mc_numbers:
            os.remove(temp_csv)
            return jsonify({
                "status": "error",
                "message": "No valid MC numbers found in the CSV"
            }), 400
        
        # Generate job ID
        job_id = uuid.uuid4().hex
        
        # Start processing in background thread
        threading.Thread(
            target=process_mc_numbers,
            args=(mc_numbers, job_id),
            daemon=True
        ).start()
        
        # Clean up the uploaded CSV
        os.remove(temp_csv)
        
        return jsonify({
            "status": "success",
            "job_id": job_id,
            "message": f"Processing started for {len(mc_numbers)} MC numbers"
        })
    
    except Exception as e:
        if os.path.exists(temp_csv):
            os.remove(temp_csv)
        return jsonify({
            "status": "error",
            "message": f"Error processing file: {str(e)}"
        }), 500

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
        return jsonify({
            "status": "error",
            "message": "Result file not found"
        }), 404
    
    return send_file(
        result_file,
        as_attachment=True,
        download_name=f"fmcsa_results_{job_id}.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/api/cleanup', methods=['POST'])
def cleanup():
    """Cleanup old temporary files (admin endpoint)"""
    if not request.headers.get('X-Admin-Key') == os.getenv('ADMIN_KEY', 'secret'):
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    try:
        cleanup_temp_files()
        return jsonify({
            "status": "success",
            "message": "Cleanup completed"
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Cleanup failed: {str(e)}"
        }), 500

if __name__ == '__main__':
    # Start cleanup thread
    def cleanup_scheduler():
        while True:
            time.sleep(CLEANUP_INTERVAL)
            cleanup_temp_files()
    
    threading.Thread(target=cleanup_scheduler, daemon=True).start()
    
    # Run the app
    app.run(host='0.0.0.0', port=5000, threaded=True)