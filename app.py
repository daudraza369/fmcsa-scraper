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
    """Scrape FMCSA data using requests and BeautifulSoup"""
    try:
        with requests.Session() as session:
            # 1. Get initial page to obtain VIEWSTATE
            response = session.get(FMCSA_URL)
            soup = BeautifulSoup(response.text, 'html.parser')
            viewstate = soup.find('input', {'name': '__VIEWSTATE'})['value']
            
            # 2. Prepare form data
            form_data = {
                '__VIEWSTATE': viewstate,
                'ctl00$MainContent$btnSearch': 'Search',
                'ctl00$MainContent$txtMC': mc_number,
                'ctl00$MainContent$searchType': 'MC'
            }
            
            # 3. Submit search
            response = session.post(FMCSA_URL, data=form_data)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 4. Extract data
            return {
                "MC Number": mc_number,
                "Company Name": extract_text(soup, 'Legal Name'),
                "Phone": extract_text(soup, 'Phone'),
                "Address": extract_text(soup, 'Physical Address'),
                "Status": extract_text(soup, 'Operating Status')
            }
            
    except Exception as e:
        print(f"Error scraping {mc_number}: {str(e)}")
        return None

def extract_text(soup, field_name):
    """Helper to extract field data"""
    try:
        td = soup.find('td', string=lambda t: field_name in str(t))
        return td.find_next_sibling('td').get_text(strip=True)
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
