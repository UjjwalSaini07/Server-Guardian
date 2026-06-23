import requests
from bs4 import BeautifulSoup
import json
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo

def is_within_allowed_hours():
    """Return True if current IST time is Mon-Fri 9:30 → 16:00."""
    ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    # Weekday < 5 (Mon-Fri) and hour between 9 and 16 (specifically 9:30 to 16:00)
    if ist.weekday() >= 5:
        return False
    
    # 9:30 AM is 9 * 60 + 30 = 570 mins. 4:00 PM is 16 * 60 = 960 mins.
    current_minutes = ist.hour * 60 + ist.minute
    return 570 <= current_minutes <= 960

def fetch_stock_data(ticker, exchange):
    try:
        url = f'https://www.screener.in/company/{ticker}/'
        response = requests.get(url, timeout=15)
        if response.status_code != 200:
            return {"error": f"Failed to fetch data. Status code: {response.status_code}"}

        soup = BeautifulSoup(response.text, 'html.parser')

        def get_text(selector):
            element = soup.select_one(selector)
            return element.text.strip() if element else None

        def parse_numeric(value):
            try:
                return float(value.replace(',', '').strip('₹').strip('%')) if value else None
            except ValueError:
                return None

        market_cap = get_text("li:-soup-contains('Market Cap') .number")
        current_price = get_text("li:-soup-contains('Current Price') .number")
        high_low = get_text("li:-soup-contains('High / Low') .nowrap.value")
        stock_pe = get_text("li:-soup-contains('Stock P/E') .number")
        dividend_yield = get_text("li:-soup-contains('Dividend Yield') .number")
        roce = get_text("li:-soup-contains('ROCE') .number")
        roe = get_text("li:-soup-contains('ROE') .number")
        face_value = get_text("li:-soup-contains('Face Value') .number")

        high, low = None, None
        if high_low and ' / ' in high_low:
            high, low = map(parse_numeric, high_low.split(' / '))

        return {
            "ticker": ticker,
            "exchange": exchange,
            "market_cap": parse_numeric(market_cap),
            "current_price": parse_numeric(current_price),
            "high": high,
            "low": low,
            "stock_pe": parse_numeric(stock_pe),
            "dividend_yield": parse_numeric(dividend_yield),
            "roce": parse_numeric(roce),
            "roe": parse_numeric(roe),
            "face_value": parse_numeric(face_value),
            "updated_at": datetime.now(ZoneInfo("Asia/Kolkata"))
        }

    except Exception as e:
        return {"error": str(e)}

def scrape_stocks(mongo_client):
    """Run the stock scraping cycle and write to MongoDB."""
    if not is_within_allowed_hours():
        print("[Stock Scraper] Skipping stock scraping (outside allowed hours Mon-Fri 9:30 - 16:00 IST).")
        return {"status": "skipped", "message": "outside allowed hours"}

    print("[Stock Scraper] Starting stock scraping cycle...")
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    ticker_file_path = os.path.join(current_dir, 'IndianStockTicker.json')

    if not os.path.exists(ticker_file_path):
        raise FileNotFoundError(f"IndianStockTicker.json not found at {ticker_file_path}")

    with open(ticker_file_path, 'r') as f:
        tickers = json.load(f)

    db = mongo_client["investiqdb"]
    collection = db["Stocks"]

    success_count = 0
    error_count = 0
    batch_size = 20
    
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        for ticker in batch:
            stock_data = fetch_stock_data(ticker, "NSE")
            if "error" not in stock_data:
                collection.update_one({"ticker": ticker}, {"$set": stock_data}, upsert=True)
                success_count += 1
            else:
                print(f"[Stock Scraper] Error fetching {ticker}: {stock_data['error']}")
                error_count += 1
            time.sleep(2)
        print(f"[Stock Scraper] Batch {i // batch_size + 1} processed")
        
    print(f"[Stock Scraper] Stock scraping cycle completed. Success: {success_count}, Errors: {error_count}")
    return {
        "status": "completed",
        "success_count": success_count,
        "error_count": error_count,
        "timestamp": datetime.now(ZoneInfo("Asia/Kolkata"))
    }
