import csv
import requests
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from openpyxl import Workbook
import os
import uuid
from bs4 import BeautifulSoup

# Configuration
TEMP_FOLDER = "temp_results"
FMCSA_URL = "https://safer.fmcsa.dot.gov/CompanySnapshot.aspx"

app = Flask(__name__)
CORS(app)

if not os.path.exists(TEMP_FOLDER):
    os.makedirs(TEMP_FOLDER)

def scrape_with_requests(mc_number):
    """Updated scraping function with current FMCSA selectors"""
    try:
        with requests.Session() as session:
            # 1. Get initial page
            response = session.get("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx")
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 2. Prepare form data with all required fields
            form_data = {
                '__VIEWSTATE': soup.find('input', {'name': '__VIEWSTATE'})['value'],
                '__VIEWSTATEGENERATOR': soup.find('input', {'name': '__VIEWSTATEGENERATOR'})['value'],
                '__EVENTVALIDATION': soup.find('input', {'name': '__EVENTVALIDATION'})['value'],
                'ctl00$MainContent$searchType': 'MC',
                'ctl00$MainContent$txtMC': mc_number,
                'ctl00$MainContent$btnSearch': 'Search'
            }
            
            # 3. Submit search
            response = session.post("https://safer.fmcsa.dot.gov/CompanySnapshot.aspx", 
                                  data=form_data,
                                  headers={
                                      'Content-Type': 'application/x-www-form-urlencoded',
                                      'User-Agent': 'Mozilla/5.0'
                                  })
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 4. Updated selectors (July 2024)
            return {
                "MC Number": mc_number,
                "Company Name": extract_text(soup, 'Legal Name:', 'td'),
                "Phone": extract_text(soup, 'Phone:', 'td'),
                "Address": extract_text(soup, 'Physical Address:', 'td'),
                "Status": extract_text(soup, 'Operating Status:', 'td')
            }
    except Exception as e:
        print(f"Error scraping {mc_number}: {str(e)}")
        return None

def extract_text(soup, text, tag='td'):
    """More robust text extraction"""
    try:
        element = soup.find(tag, string=lambda t: text in str(t))
        return element.find_next(tag).get_text(strip=True) if element else "NOT FOUND"
    except:
        return "NOT FOUND"

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
        
        # Initialize Excel
        result_file = f"{TEMP_FOLDER}/{uuid.uuid4()}.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["MC Number", "Company Name", "Phone", "Address", "Status"])
        found = 0
        
        for mc in mc_numbers:
            data = scrape_with_requests(mc)
            if data and "AUTHORIZED" in data["Status"].upper():
                sheet.append(list(data.values()))
                found += 1
        
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
