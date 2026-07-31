"""
Metrika Weekly Report — анализ Метрики за неделю → Google Docs.
"""
import os, json, logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

METRIKA_TOKEN = os.getenv('YANDEX_METRIKA_TOKEN')
COUNTERS = {'amadey': '94834593', 'divaninfo': '63403'}
DOCS_FOLDER = 'Reports'

def metrika_request(counter_id: str, params: dict) -> dict:
    url = f'https://api-metrika.yandex.net/stat/v1/data'
    headers = {'Authorization': f'OAuth {METRIKA_TOKEN}'}
    params['ids'] = counter_id
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_week_data(counter_id: str) -> dict:
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)
    
    params = {
        'date1': two_weeks_ago.strftime('%Y%m%d'),
        'date2': week_ago.strftime('%Y%m%d'),
        'metrics': 'ym:s:visits,ym:s:pageviews,ym:s:users,ym:s:bounceRate,ym:s:avgVisitDurationSeconds',
        'dimensions': 'ym:s:date',
        'sort': 'ym:s:date',
        'limit': 30
    }
    prev = metrika_request(counter_id, params)
    
    params['date1'] = week_ago.strftime('%Y%m%d')
    params['date2'] = now.strftime('%Y%m%d')
    curr = metrika_request(counter_id, params)
    
    return {'prev': prev, 'curr': curr}

def summarize(data: dict, site_name: str) -> str:
    try:
        prev_data = data['prev']['data']
        curr_data = data['curr']['data']
        
        if not prev_data or not curr_data:
            return f"{site_name}: недостаточно данных"
        
        def extract_totals(d):
            return {
                'visits': sum(int(row['metrics'][0]) for row in d),
                'pageviews': sum(int(row['metrics'][1]) for row in d),
                'users': sum(int(row['metrics'][2]) for row in d),
            }
        
        prev_totals = extract_totals(prev_data)
        curr_totals = extract_totals(curr_data)
        
        visits_change = ((curr_totals['visits'] - prev_totals['visits']) / prev_totals['visits'] * 100) if prev_totals['visits'] else 0
        
        summary = f"""### {site_name}

| Метрика | Прошлая неделя | Эта неделя | Изменение |
|---------|---------------|------------|-----------|
| Визиты | {prev_totals['visits']:,} | {curr_totals['visits']:,} | {visits_change:+.1f}% |
| Просмотры | {prev_totals['pageviews']:,} | {curr_totals['pageviews']:,} | — |
| Пользователи | {prev_totals['users']:,} | {curr_totals['users']:,} | — |

**Вывод:** """
        
        if visits_change > 10:
            summary += f"Трафик вырос на {visits_change:.0f}% — отличная динамика!"
        elif visits_change < -10:
            summary += f"Трафик упал на {abs(visits_change):.0f}% — нужно разобраться."
        else:
            summary += "Трафик стабилен."
        
        return summary
    except Exception as e:
        return f"{site_name}: ошибка анализа ({e})"

def main():
    load_dotenv()
    if not METRIKA_TOKEN:
        log.error("YANDEX_METRIKA_TOKEN not set"); return
    
    log.info("Fetching Metrika data...")
    report_sections = []
    
    for site_name, counter_id in COUNTERS.items():
        data = get_week_data(counter_id)
        section = summarize(data, site_name)
        report_sections.append(section)
        log.info(f"{site_name}: done")
    
    report_date = datetime.now().strftime('%Y-%m-%d')
    report_body = f"""# Отчёт Метрики — {report_date}

## Период: последние 7 дней vs предыдущие 7 дней

{' '.join(report_sections)}

---
*Auto-generated {report_date}*
"""
    
    # Save locally
    out_path = Path(__file__).parent / 'metrika_report.md'
    out_path.write_text(report_body, encoding='utf-8')
    log.info(f"Report saved to {out_path}")
    
    # Upload to Google Docs via Composio
    try:
        from composio_client import ComposioClient
        client = ComposioClient(api_key=os.getenv('COMPOSIO_API_KEY'))
        doc = client.google_docs.create_document(
            title=f"Метрика {report_date}",
            content=report_body,
            folder_name=DOCS_FOLDER
        )
        doc_url = doc.get('document', {}).get('url', 'unknown')
        log.info(f"Doc created: {doc_url}")
        
        # Send Telegram notification
        import requests as tg_req
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        chat_id = os.getenv('TELEGRAM_CHAT_ID')
        if bot_token and chat_id:
            msg = f"📊 *Отчёт Метрики {report_date}*\n\n{chr(10).join(s[:100] for s in report_sections)}\n\n📄 Документ: {doc_url}"
            tg_req.post(f'https://api.telegram.org/bot{bot_token}/sendMessage', json={
                'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'
            })
    except Exception as e:
        log.error(f"Doc/Telegram error (non-fatal): {e}")

if __name__ == '__main__':
    main()