#!/usr/bin/env python3
"""Trello helper using REST API.

Usage:
  python3 trello_helper.py boards                          # List boards
  python3 trello_helper.py lists <board_id>                 # List lists on board
  python3 trello_helper.py cards <list_id>                  # List cards in list
  python3 trello_helper.py card <card_id>                   # Get card details+desc
  python3 trello_helper.py comments <card_id>               # Get card comments
  python3 trello_helper.py attachments <card_id>            # List card attachments
  python3 trello_helper.py checklist <checklist_id>         # Get checklist items
  python3 trello_helper.py search <query> [board_id]        # Search cards
  python3 trello_helper.py archive <card_id>                # Archive a card
  python3 trello_helper.py move <card_id> <list_id>         # Move card to list
  python3 trello_helper.py add_comment <card_id> <text>     # Add comment to card
"""

import json, os, sys, urllib.request, urllib.parse

API_KEY = os.environ.get('TRELLO_API_KEY')
TOKEN = os.environ.get('TRELLO_TOKEN')
BASE = 'https://api.trello.com/1'

if not API_KEY or not TOKEN:
    print("ERROR: Set TRELLO_API_KEY and TRELLO_TOKEN env vars", file=sys.stderr)
    sys.exit(1)

def api(path, params=None):
    url = f"{BASE}{path}"
    p = {'key': API_KEY, 'token': TOKEN}
    if params:
        p.update(params)
    url += '?' + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def put_api(path, body):
    """PUT request with JSON body."""
    url = f"{BASE}{path}?key={API_KEY}&token={TOKEN}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method='PUT',
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def fmt_card(c):
    labels = ' '.join(f"[{l['name']}]" for l in c.get('labels', []))
    due = f" 📅{c['due'][:10]}" if c.get('due') else ''
    print(f"  🃏 {c['name']} ({c['id']}){due} {labels}")
    if c.get('desc'):
        desc_short = c['desc'][:120].replace('\n', ' ')
        print(f"     {desc_short}{'...' if len(c['desc'])>120 else ''}")

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'boards'

    if cmd == 'boards':
        for b in api('/members/me/boards'):
            print(f"📋 {b['name']} ({b['id']})")

    elif cmd == 'lists':
        for lst in api(f"/boards/{sys.argv[2]}/lists"):
            print(f"📋 {lst['name']} ({lst['id']})")

    elif cmd == 'cards':
        cards = api(f"/lists/{sys.argv[2]}/cards")
        for c in cards:
            fmt_card(c)

    elif cmd == 'card':
        c = api(f"/cards/{sys.argv[2]}")
        labels = ' '.join(f"[{l['name']}]" for l in c.get('labels', []))
        due = f" 📅{c['due'][:10]}" if c.get('due') else ''
        print(f"🃏 {c['name']} {due} {labels}")
        print(f"   Lista: {c.get('idList', '?')}")
        print(f"   URL: {c.get('shortUrl', '')}")
        if c.get('desc'):
            print(f"\n📝 Descrição:\n{c['desc']}")
        # Checklists
        if c.get('idChecklists'):
            for cl in c['idChecklists']:
                print(f"\n✅ Checklist: {cl['name']}")
                for item in cl.get('checkItems', []):
                    chk = '✓' if item['state'] == 'complete' else '○'
                    print(f"   {chk} {item['name']}")

    elif cmd == 'comments':
        actions = api(f"/cards/{sys.argv[2]}/actions", {
            'filter': 'commentCard',
            'limit': '20'
        })
        if not actions:
            print("  Nenhum comentário.")
        for a in actions:
            author = a['memberCreator']['fullName']
            date = a['date'][:16].replace('T', ' ')
            text = a['data']['text']
            print(f"  💬 {author} ({date}):")
            for line in text.split('\n'):
                print(f"    {line}")
            print()

    elif cmd == 'attachments':
        for att in api(f"/cards/{sys.argv[2]}/attachments"):
            print(f"  📎 {att['name']} -> {att['url']}")

    elif cmd == 'checklist':
        cl = api(f"/checklists/{sys.argv[2]}")
        print(f"✅ {cl['name']}")
        for item in cl.get('checkItems', []):
            chk = '✓' if item['state'] == 'complete' else '○'
            print(f"  {chk} {item['name']}")

    elif cmd == 'search':
        query = sys.argv[2]
        params = {'query': query, 'modelTypes': 'cards'}
        if len(sys.argv) > 3:
            params['idBoards'] = sys.argv[3]
        results = api('/search', params)
        for c in results.get('cards', []):
            fmt_card(c)
        if not results.get('cards'):
            print("  Nenhum card encontrado.")

    elif cmd == 'archive':
        c = put_api(f"/cards/{sys.argv[2]}", {'closed': True})
        print(f"Arquivado: {c['name']}")

    elif cmd == 'move':
        c = put_api(f"/cards/{sys.argv[2]}", {'idList': sys.argv[3]})
        print(f"Movido: {c['name']} -> lista {c['idList']}")

    elif cmd == 'add_comment':
        text = sys.argv[3]
        data = json.dumps({'text': text}).encode()
        url = f"{BASE}/cards/{sys.argv[2]}/actions/comments?key={API_KEY}&token={TOKEN}"
        req = urllib.request.Request(url, data=data,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as r:
            json.loads(r.read())
        print(f"Comentário adicionado em {sys.argv[2]}")

    else:
        print(f"Unknown command: {cmd}")
