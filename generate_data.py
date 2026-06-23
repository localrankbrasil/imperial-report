"""
Imperial Brazilian Cowhide Rug — Report Data Generator
Pulls from Google Ads, Search Console, Merchant Center (and GA4 if configured).
Generates report/data/YYYY-MM.json consumed by the dashboard.

Usage:
    python3 generate_data.py              # current month
    python3 generate_data.py 2026-05      # specific month
"""

import json
import os
import sys
import calendar
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

import requests

# ── Credentials ───────────────────────────────────────────────────────
REFRESH_TOKEN   = os.getenv('REPORT_REFRESH_TOKEN')
CLIENT_ID       = os.getenv('REPORT_CLIENT_ID')
CLIENT_SECRET   = os.getenv('REPORT_CLIENT_SECRET')
ADS_DEV_TOKEN   = os.getenv('GOOGLE_ADS_DEVELOPER_TOKEN')
ADS_LOGIN_CID   = os.getenv('GOOGLE_ADS_LOGIN_CUSTOMER_ID')
ADS_CUSTOMER_ID = os.getenv('GOOGLE_ADS_CUSTOMER_ID')
GMC_MERCHANT_ID = os.getenv('GMC_MERCHANT_ID')
GSC_SITE_URL    = os.getenv('GSC_SITE_URL', 'https://imperialbraziliancowhiderug.com/')
GA4_PROPERTY_ID = os.getenv('GA4_PROPERTY_ID', '')


def get_token():
    r = requests.post('https://oauth2.googleapis.com/token', data={
        'grant_type':    'refresh_token',
        'refresh_token': REFRESH_TOKEN,
        'client_id':     CLIENT_ID,
        'client_secret': CLIENT_SECRET,
    })
    r.raise_for_status()
    return r.json()['access_token']


def month_range(period: str):
    """Return (start_date, end_date, prev_start, prev_end) for a YYYY-MM period."""
    year, month = int(period[:4]), int(period[5:7])
    start = date(year, month, 1)
    end   = date(year, month, calendar.monthrange(year, month)[1])
    today = date.today()
    if end > today:
        end = today

    # previous month
    if month == 1:
        py, pm = year - 1, 12
    else:
        py, pm = year, month - 1
    prev_start = date(py, pm, 1)
    prev_end   = date(py, pm, calendar.monthrange(py, pm)[1])

    return start, end, prev_start, prev_end


# ── Google Ads ────────────────────────────────────────────────────────

def ads_query(token: str, gaql: str) -> list:
    url = f'https://googleads.googleapis.com/v24/customers/{ADS_CUSTOMER_ID}/googleAds:search'
    headers = {
        'Authorization':      f'Bearer {token}',
        'developer-token':    ADS_DEV_TOKEN,
        'login-customer-id':  ADS_LOGIN_CID,
        'Content-Type':       'application/json',
    }
    r = requests.post(url, headers=headers, json={'query': gaql})
    if r.status_code != 200:
        print(f'  ⚠ Google Ads query error {r.status_code}: {r.text[:200]}')
        return []
    return r.json().get('results', [])


def fetch_ads(token: str, start: date, end: date, prev_start: date, prev_end: date) -> dict:
    print('  → Google Ads: campaign summary...')

    def camp_query(s, e):
        return f"""
SELECT
  campaign.id, campaign.name, campaign.status,
  metrics.cost_micros, metrics.clicks, metrics.impressions,
  metrics.ctr, metrics.average_cpc,
  metrics.conversions, metrics.conversions_value
FROM campaign
WHERE segments.date BETWEEN '{s}' AND '{e}'
  AND campaign.advertising_channel_type = 'SHOPPING'
  AND campaign.status != 'REMOVED'
"""
    def daily_query(s, e):
        return f"""
SELECT
  segments.date,
  metrics.cost_micros, metrics.clicks, metrics.impressions,
  metrics.conversions, metrics.conversions_value
FROM campaign
WHERE segments.date BETWEEN '{s}' AND '{e}'
  AND campaign.advertising_channel_type = 'SHOPPING'
"""
    def terms_query(s, e):
        return f"""
SELECT
  search_term_view.search_term,
  metrics.clicks, metrics.impressions, metrics.ctr, metrics.conversions
FROM search_term_view
WHERE segments.date BETWEEN '{s}' AND '{e}'
ORDER BY metrics.clicks DESC
LIMIT 15
"""

    def parse_camps(rows):
        camps = []
        totals = {'cost': 0, 'clicks': 0, 'impressions': 0,
                  'conversions': 0, 'conversion_value': 0}
        for r in rows:
            m = r.get('metrics', {})
            c = r.get('campaign', {})
            cost   = int(m.get('costMicros', 0)) / 1e6
            clicks = int(m.get('clicks', 0))
            impr   = int(m.get('impressions', 0))
            conv   = float(m.get('conversions', 0))
            cval   = float(m.get('conversionsValue', 0))
            roas   = round(cval / cost, 2) if cost > 0 else 0
            ctr    = round(clicks / impr * 100, 2) if impr > 0 else 0
            cpc    = round(cost / clicks, 2) if clicks > 0 else 0
            camps.append({
                'name': c.get('name', ''),
                'status': c.get('status', ''),
                'cost': round(cost, 2),
                'clicks': clicks,
                'impressions': impr,
                'ctr': ctr,
                'avg_cpc': cpc,
                'conversions': round(conv, 1),
                'conversion_value': round(cval, 2),
                'roas': roas,
            })
            totals['cost']             += cost
            totals['clicks']           += clicks
            totals['impressions']      += impr
            totals['conversions']      += conv
            totals['conversion_value'] += cval
        return camps, totals

    curr_rows = ads_query(token, camp_query(start, end))
    prev_rows = ads_query(token, camp_query(prev_start, prev_end))
    camps, curr = parse_camps(curr_rows)
    _, prev = parse_camps(prev_rows)

    curr_ctr  = round(curr['clicks'] / curr['impressions'] * 100, 2) if curr['impressions'] > 0 else 0
    curr_cpc  = round(curr['cost'] / curr['clicks'], 2) if curr['clicks'] > 0 else 0
    curr_roas = round(curr['conversion_value'] / curr['cost'], 2) if curr['cost'] > 0 else 0
    prev_ctr  = round(prev['clicks'] / prev['impressions'] * 100, 2) if prev['impressions'] > 0 else 0
    prev_cpc  = round(prev['cost'] / prev['clicks'], 2) if prev['clicks'] > 0 else 0
    prev_roas = round(prev['conversion_value'] / prev['cost'], 2) if prev['cost'] > 0 else 0

    # Daily
    print('  → Google Ads: daily breakdown...')
    daily_rows = ads_query(token, daily_query(start, end))
    daily = {}
    for r in daily_rows:
        d = r.get('segments', {}).get('date', '')
        m = r.get('metrics', {})
        if d not in daily:
            daily[d] = {'date': d, 'cost': 0, 'clicks': 0, 'impressions': 0,
                        'conversions': 0, 'conversion_value': 0}
        daily[d]['cost']             += int(m.get('costMicros', 0)) / 1e6
        daily[d]['clicks']           += int(m.get('clicks', 0))
        daily[d]['impressions']      += int(m.get('impressions', 0))
        daily[d]['conversions']      += float(m.get('conversions', 0))
        daily[d]['conversion_value'] += float(m.get('conversionsValue', 0))

    daily_list = []
    for d in sorted(daily.keys()):
        e = daily[d]
        daily_list.append({
            'date': d,
            'cost': round(e['cost'], 2),
            'clicks': e['clicks'],
            'impressions': e['impressions'],
            'conversions': round(e['conversions'], 1),
            'conversion_value': round(e['conversion_value'], 2),
        })

    # Search terms
    print('  → Google Ads: search terms...')
    term_rows = ads_query(token, terms_query(start, end))
    terms = []
    for r in term_rows:
        m = r.get('metrics', {})
        stv = r.get('searchTermView', {})
        clicks = int(m.get('clicks', 0))
        impr   = int(m.get('impressions', 0))
        terms.append({
            'term':        stv.get('searchTerm', ''),
            'clicks':      clicks,
            'impressions': impr,
            'ctr':         round(clicks / impr * 100, 2) if impr > 0 else 0,
            'conversions': round(float(m.get('conversions', 0)), 1),
        })

    return {
        'summary': {
            'cost':                  round(curr['cost'], 2),
            'cost_prev':             round(prev['cost'], 2),
            'clicks':                curr['clicks'],
            'clicks_prev':           prev['clicks'],
            'impressions':           curr['impressions'],
            'impressions_prev':      prev['impressions'],
            'ctr':                   curr_ctr,
            'ctr_prev':              prev_ctr,
            'avg_cpc':               curr_cpc,
            'avg_cpc_prev':          prev_cpc,
            'conversions':           round(curr['conversions'], 1),
            'conversions_prev':      round(prev['conversions'], 1),
            'conversion_value':      round(curr['conversion_value'], 2),
            'conversion_value_prev': round(prev['conversion_value'], 2),
            'roas':                  curr_roas,
            'roas_prev':             prev_roas,
        },
        'campaigns':    camps,
        'daily':        daily_list,
        'search_terms': terms,
    }


# ── Search Console ────────────────────────────────────────────────────

def fetch_gsc(token: str, start: date, end: date, prev_start: date, prev_end: date) -> dict:
    print('  → Search Console...')
    url = f'https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(GSC_SITE_URL, safe="")}/searchAnalytics/query'
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def query(s, e, dims):
        r = requests.post(url, headers=headers, json={
            'startDate': str(s), 'endDate': str(e),
            'dimensions': dims, 'rowLimit': 25,
        })
        return r.json().get('rows', []) if r.status_code == 200 else []

    def totals(s, e):
        r = requests.post(url, headers=headers, json={
            'startDate': str(s), 'endDate': str(e), 'dimensions': [],
        })
        if r.status_code == 200 and r.json().get('rows'):
            row = r.json()['rows'][0]
            return {'clicks': row['clicks'], 'impressions': row['impressions'],
                    'ctr': round(row['ctr'] * 100, 2), 'avg_position': round(row['position'], 1)}
        return {'clicks': 0, 'impressions': 0, 'ctr': 0, 'avg_position': 0}

    curr_sum  = totals(start, end)
    prev_sum  = totals(prev_start, prev_end)

    daily_rows = query(start, end, ['date'])
    daily = [{'date': r['keys'][0],
               'clicks': r['clicks'],
               'impressions': r['impressions']} for r in daily_rows]

    query_rows = query(start, end, ['query'])
    queries = [{'query': r['keys'][0], 'clicks': r['clicks'],
                 'impressions': r['impressions'],
                 'ctr': round(r['ctr'] * 100, 2),
                 'position': round(r['position'], 1)} for r in query_rows]

    page_rows = query(start, end, ['page'])
    pages = [{'page': r['keys'][0], 'clicks': r['clicks'],
               'impressions': r['impressions'],
               'ctr': round(r['ctr'] * 100, 2),
               'position': round(r['position'], 1)} for r in page_rows]

    return {
        'summary': {
            'clicks':           curr_sum['clicks'],
            'clicks_prev':      prev_sum['clicks'],
            'impressions':      curr_sum['impressions'],
            'impressions_prev': prev_sum['impressions'],
            'ctr':              curr_sum['ctr'],
            'ctr_prev':         prev_sum['ctr'],
            'avg_position':     curr_sum['avg_position'],
            'avg_position_prev':prev_sum['avg_position'],
        },
        'daily':       sorted(daily, key=lambda x: x['date']),
        'top_queries': queries,
        'top_pages':   pages,
    }


# ── Merchant Center ───────────────────────────────────────────────────

def fetch_gmc(token: str) -> dict:
    print('  → Merchant Center: product status...')
    headers = {'Authorization': f'Bearer {token}'}
    url = f'https://shoppingcontent.googleapis.com/content/v2.1/{GMC_MERCHANT_ID}/productstatuses'
    params = {'maxResults': 250, 'includeInvalidInsertedItems': True}

    all_products = []
    page_token = None
    while True:
        if page_token:
            params['pageToken'] = page_token
        r = requests.get(url, headers=headers, params=params)
        if r.status_code != 200:
            print(f'  ⚠ GMC error {r.status_code}')
            break
        data = r.json()
        all_products.extend(data.get('resources', []))
        page_token = data.get('nextPageToken')
        if not page_token:
            break

    approved = disapproved = limited = under_review = 0
    by_category = {}
    for p in all_products:
        dest = p.get('destinationStatuses', [{}])[0]
        status = dest.get('status', 'disapproved')
        # product type from title
        title = p.get('title', '')
        if 'Small'    in title: cat = 'Cowhide Rug Small'
        elif 'Average' in title: cat = 'Cowhide Rug Average'
        elif 'Large'   in title: cat = 'Cowhide Rug Large'
        elif 'Patchwork' in title or 'Mandala' in title: cat = 'Round Cowhide Patchwork'
        else: cat = 'Other'

        if cat not in by_category:
            by_category[cat] = {'category': cat, 'count': 0, 'approved': 0, 'disapproved': 0}
        by_category[cat]['count'] += 1

        if status in ('active', 'approved', 'eligibleLimited'):
            approved += 1
            by_category[cat]['approved'] += 1
        elif status == 'disapproved':
            disapproved += 1
            by_category[cat]['disapproved'] += 1
        elif status == 'pending':
            under_review += 1
        else:
            limited += 1

    return {
        'approved':    approved,
        'disapproved': disapproved,
        'limited':     limited,
        'under_review': under_review,
        'total':       len(all_products),
        'by_category': list(by_category.values()),
        'top_products': [],  # requires Ads API product-level query
    }


# ── GA4 ───────────────────────────────────────────────────────────────

def fetch_ga4(token: str, start: date, end: date, prev_start: date, prev_end: date) -> dict:
    if not GA4_PROPERTY_ID:
        print('  ⚠ GA4_PROPERTY_ID not set — skipping GA4 data')
        return _empty_ga4()

    print(f'  → GA4 property {GA4_PROPERTY_ID}...')
    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY_ID}:runReport'
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    def run(s, e, dims, metrics):
        body = {
            'dateRanges': [{'startDate': str(s), 'endDate': str(e)}],
            'dimensions': [{'name': d} for d in dims],
            'metrics':    [{'name': m} for m in metrics],
            'limit': 50,
        }
        r = requests.post(url, headers=headers, json=body)
        if r.status_code != 200:
            print(f'    GA4 error {r.status_code}: {r.text[:150]}')
            return []
        return r.json().get('rows', [])

    # Summary
    sum_rows = run(start, end, [], ['sessions','activeUsers','totalRevenue','transactions','bounceRate'])
    prev_sum = run(prev_start, prev_end, [], ['sessions','activeUsers','totalRevenue','transactions','bounceRate'])

    def parse_sum(rows):
        if not rows:
            return {'sessions': 0, 'users': 0, 'revenue': 0, 'transactions': 0, 'bounce_rate': 0}
        vals = rows[0].get('metricValues', [])
        return {
            'sessions':     int(vals[0]['value']) if len(vals) > 0 else 0,
            'users':        int(vals[1]['value']) if len(vals) > 1 else 0,
            'revenue':      round(float(vals[2]['value']), 2) if len(vals) > 2 else 0,
            'transactions': int(vals[3]['value']) if len(vals) > 3 else 0,
            'bounce_rate':  round(float(vals[4]['value']) * 100, 1) if len(vals) > 4 else 0,
        }

    curr = parse_sum(sum_rows)
    prev = parse_sum(prev_sum)
    cr   = round(curr['transactions'] / curr['sessions'] * 100, 2) if curr['sessions'] > 0 else 0
    pcr  = round(prev['transactions'] / prev['sessions'] * 100, 2) if prev['sessions'] > 0 else 0

    # Daily
    daily_rows = run(start, end, ['date'], ['sessions','totalRevenue','transactions'])
    daily = []
    for r in daily_rows:
        d = r['dimensionValues'][0]['value']
        v = r['metricValues']
        daily.append({
            'date':         f"{d[:4]}-{d[4:6]}-{d[6:]}",
            'sessions':     int(v[0]['value']),
            'revenue':      round(float(v[1]['value']), 2),
            'transactions': int(v[2]['value']),
        })

    # Channels
    ch_rows = run(start, end, ['sessionDefaultChannelGroup'],
                  ['sessions','totalRevenue','transactions'])
    channels = []
    for r in ch_rows:
        ch = r['dimensionValues'][0]['value']
        v  = r['metricValues']
        sess = int(v[0]['value'])
        rev  = round(float(v[1]['value']), 2)
        tran = int(v[2]['value'])
        channels.append({
            'channel': ch, 'sessions': sess, 'revenue': rev,
            'transactions': tran,
            'conversion_rate': round(tran / sess * 100, 2) if sess > 0 else 0,
        })

    return {
        'summary': {
            'sessions':              curr['sessions'],
            'sessions_prev':         prev['sessions'],
            'users':                 curr['users'],
            'users_prev':            prev['users'],
            'revenue':               curr['revenue'],
            'revenue_prev':          prev['revenue'],
            'transactions':          curr['transactions'],
            'transactions_prev':     prev['transactions'],
            'conversion_rate':       cr,
            'conversion_rate_prev':  pcr,
            'avg_session_duration':  '—',
            'bounce_rate':           curr['bounce_rate'],
            'bounce_rate_prev':      prev['bounce_rate'],
        },
        'channels': channels,
        'daily':    sorted(daily, key=lambda x: x['date']),
        'funnel': {
            'sessions':         curr['sessions'],
            'product_views':    0,
            'add_to_cart':      0,
            'checkout_started': 0,
            'purchase':         curr['transactions'],
        },
    }


def _empty_ga4() -> dict:
    return {
        'summary': {
            'sessions': 0, 'sessions_prev': 0, 'users': 0, 'users_prev': 0,
            'revenue': 0, 'revenue_prev': 0, 'transactions': 0, 'transactions_prev': 0,
            'conversion_rate': 0, 'conversion_rate_prev': 0,
            'avg_session_duration': '—', 'bounce_rate': 0, 'bounce_rate_prev': 0,
        },
        'channels': [],
        'daily': [],
        'funnel': {'sessions': 0, 'product_views': 0, 'add_to_cart': 0, 'checkout_started': 0, 'purchase': 0},
        '_note': 'GA4 not configured. Set GA4_PROPERTY_ID to enable.',
    }


# ── Main ──────────────────────────────────────────────────────────────

def main():
    period = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime('%Y-%m')
    start, end, prev_start, prev_end = month_range(period)

    print(f'\n📊 Generating report for {period} ({start} → {end})\n')

    token = get_token()

    ads     = fetch_ads(token, start, end, prev_start, prev_end)
    gsc     = fetch_gsc(token, start, end, prev_start, prev_end)
    gmc     = fetch_gmc(token)
    ga4     = fetch_ga4(token, start, end, prev_start, prev_end)

    # Executive summary uses Ads data for revenue/ROAS (since GA4 may not be configured)
    rev_source = ga4['summary']['revenue'] if GA4_PROPERTY_ID else ads['summary']['conversion_value']
    rev_prev   = ga4['summary']['revenue_prev'] if GA4_PROPERTY_ID else ads['summary']['conversion_value_prev']
    orders     = ga4['summary']['transactions'] if GA4_PROPERTY_ID else int(ads['summary']['conversions'])
    orders_prev = ga4['summary']['transactions_prev'] if GA4_PROPERTY_ID else int(ads['summary']['conversions_prev'])

    output = {
        'meta': {
            'period':       period,
            'period_label': date(int(period[:4]), int(period[5:7]), 1).strftime('%B %Y'),
            'generated_at': str(date.today()),
            'client':       'Imperial Brazilian Cowhide Rug',
            'currency':     'USD',
        },
        'executive': {
            'revenue':              round(rev_source, 2),
            'revenue_prev':         round(rev_prev, 2),
            'roas':                 ads['summary']['roas'],
            'roas_prev':            ads['summary']['roas_prev'],
            'cost':                 ads['summary']['cost'],
            'cost_prev':            ads['summary']['cost_prev'],
            'orders':               orders,
            'orders_prev':          orders_prev,
            'avg_order_value':      round(rev_source / orders, 2) if orders > 0 else 0,
            'avg_order_value_prev': round(rev_prev / orders_prev, 2) if orders_prev > 0 else 0,
            'paid_clicks':          ads['summary']['clicks'],
            'paid_clicks_prev':     ads['summary']['clicks_prev'],
            'impressions':          ads['summary']['impressions'],
            'impressions_prev':     ads['summary']['impressions_prev'],
        },
        'google_ads':      ads,
        'merchant_center': gmc,
        'ga4':             ga4,
        'search_console':  gsc,
    }

    os.makedirs('data', exist_ok=True)
    out_path = f'data/{period}.json'
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f'\n✅  Saved {out_path}')
    print(f'   Ads: ${ads["summary"]["cost"]:.2f} spend · {ads["summary"]["clicks"]} clicks · {ads["summary"]["roas"]:.2f}x ROAS')
    print(f'   GMC: {gmc["approved"]} approved · {gmc["disapproved"]} disapproved')
    print(f'   GSC: {gsc["summary"]["clicks"]} organic clicks · pos {gsc["summary"]["avg_position"]}')
    if GA4_PROPERTY_ID:
        print(f'   GA4: {ga4["summary"]["sessions"]} sessions · ${ga4["summary"]["revenue"]:.2f} revenue')
    else:
        print('   GA4: not configured (set GA4_PROPERTY_ID)')


if __name__ == '__main__':
    main()
