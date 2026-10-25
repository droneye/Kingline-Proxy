import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from db import SubscriptionDB
load_dotenv(Path(__file__).with_name('.env'))
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
if not BOT_TOKEN:
    raise SystemExit('BOT_TOKEN is required')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
DB_PATH = os.environ['DB_PATH']
db = SubscriptionDB(DB_PATH)
FULL_TARIFF_PRICE = int(os.getenv('FULL_TARIFF_PRICE', '300'))
SUB_BASE = os.environ['SUB_BASE'].rstrip('/') + '/'
XUI_SUB_SUFFIX = ''
INBOUND_IDS = [1]
# Transitional interface fields; no credentials for old panels are present.
XUI_PANELS = [{'tag': 'REMNA', 'url': os.getenv('REMNA_API_URL', 'http://remnawave:3000/api'),
               'username': 'native-remnawave', 'password': 'token-file-managed', 'verify_ssl': True}]
XUI_SERVERS = XUI_PANELS
XUI_URL = XUI_PANELS[0]['url']
YK_SHOP_ID = os.getenv('YK_SHOP_ID', '')
YK_SECRET_KEY = os.getenv('YK_SECRET_KEY', '')
YK_RETURN_URL = os.getenv('KINGLINE_SITE_URL', '')
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
logger = logging.getLogger('kingline-staging')
def collect_xui_panels():
    return XUI_PANELS
