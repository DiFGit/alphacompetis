from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://alphacompetitionservices.com/calendar/', wait_until='domcontentloaded')
    page.wait_for_timeout(6000)
    soup = BeautifulSoup(page.content(), 'html.parser')
    browser.close()

for h2 in soup.find_all('h2')[:5]:
    sib = h2.find_next_sibling()
    strongs = sib.find_all('strong') if sib else []
    print(repr(h2.get_text(strip=True)), '|', [s.get_text(strip=True) for s in strongs])

input('prima enter')