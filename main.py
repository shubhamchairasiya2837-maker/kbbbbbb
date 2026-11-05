# Kaun Banega Bhikhari server. Put this file and index.html (the game page) in the same folder, then run it.
#   pip install flask
#   python main.py
# Host login, shared host-made modes, sounds, results and invite-code rooms all live here.
import os, re, json, sqlite3, hmac, random, time, base64
from datetime import datetime, timezone
from flask import Flask, request, jsonify, Response

# ---- Change these 2 lines (keep the quotes) ----
HOST_ID   = os.environ.get('HOST_ID',   'shubham05')
HOST_PASS = os.environ.get('HOST_PASS', '05112006')
# ------------------------------------------------

SOUND_KEYS = ('greet', 'right', 'wrong', 'u1W', 'u2W') + tuple(u + l for u in ('u1', 'u2') for l in 'ABCD')
HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, 'kbb.db')
app = Flask(__name__)


def db():
    c = sqlite3.connect(DB_FILE, timeout=10)
    c.execute('CREATE TABLE IF NOT EXISTS visits (time TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS plays (time TEXT, name TEXT, quiz TEXT, '
              'points INTEGER, correct INTEGER, wrong INTEGER, answers TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS rooms (code TEXT PRIMARY KEY, state TEXT, updated REAL)')
    return c


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def same(a, b):
    return hmac.compare_digest(str(a).encode('utf-8'), str(b).encode('utf-8'))


def host_ok(i, p):
    return same(str(i).strip().lower(), HOST_ID.lower()) and same(str(p).strip(), HOST_PASS)


def body():
    try:
        return json.loads(request.get_data(as_text=True) or '{}')
    except ValueError:
        return None


@app.after_request
def cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp


@app.route('/')
def index():
    with open(os.path.join(HERE, 'index.html'), encoding='utf-8') as f:
        html = f.read()
    return Response(html.replace('<head>', '<head><script>window.KBB_API="/api";</script>', 1),
                    mimetype='text/html')


# ---------- visits, results, host players list ----------
@app.route('/api', methods=['GET', 'POST', 'OPTIONS'], strict_slashes=False)
def api():
    if request.method == 'OPTIONS':
        return '', 204
    if request.method == 'POST':
        d = body()
        if d is None:
            return 'bad request', 400
        c = db()
        if d.get('type') == 'visit':
            c.execute('INSERT INTO visits VALUES (?)', (now(),))
        else:
            c.execute('INSERT INTO plays VALUES (?,?,?,?,?,?,?)', (
                now(), str(d.get('name', ''))[:24], str(d.get('quiz', ''))[:40],
                num(d.get('points')), num(d.get('correct')), num(d.get('wrong')),
                str(d.get('answers', ''))[:40]))
        c.commit(); c.close()
        return 'ok'
    if request.args.get('action') != 'players':
        return 'working'
    if not host_ok(request.args.get('id', ''), request.args.get('pass', '')):
        return jsonify(ok=False, error='Wrong ID or password')
    c = db()
    visits = c.execute('SELECT COUNT(*) FROM visits').fetchone()[0]
    rows = c.execute('SELECT time, name, quiz, points, correct, wrong, answers FROM plays '
                     'ORDER BY rowid DESC LIMIT 1000').fetchall()
    c.close()
    plays = [dict(time=r[0], name=r[1], quiz=r[2], points=r[3], correct=r[4], wrong=r[5], answers=r[6])
             for r in rows]
    return jsonify(ok=True, visits=visits, plays=plays)


# ---------- host-made modes and questions (shared by every player) ----------
def clean_modes(raw):
    out = []
    for m in (raw if isinstance(raw, list) else [])[:50]:
        qs = []
        for x in (m.get('qs') or [])[:200]:
            o = [str(t)[:120] for t in (x.get('o') or [])[:4]]
            if len(o) == 4 and str(x.get('q', '')).strip():
                qs.append(dict(q=str(x['q'])[:300], o=o, a=min(max(num(x.get('a')), 0), 3)))
        out.append(dict(id=str(m.get('id', ''))[:40], name=str(m.get('name', ''))[:40], hue=num(m.get('hue')) % 361,
                        free=bool(m.get('free', True)), price=num(m.get('price')), qs=qs))
    return out


@app.route('/modes', methods=['GET', 'POST', 'OPTIONS'], strict_slashes=False)
def modes():
    if request.method == 'OPTIONS':
        return '', 204
    c = db()
    if request.method == 'GET':
        r = c.execute("SELECT value FROM kv WHERE key='modes'").fetchone()
        rr = c.execute("SELECT value FROM kv WHERE key='roles'").fetchone()
        c.close()
        return jsonify(ok=True, modes=json.loads(r[0]) if r else [], roles=json.loads(rr[0]) if rr else {})
    d = body()
    if d is None or not host_ok(d.get('id', ''), d.get('pass', '')):
        c.close()
        return jsonify(ok=False, error='Wrong ID or password')
    c.execute("REPLACE INTO kv VALUES ('modes', ?)", (json.dumps(clean_modes(d.get('modes'))),))
    roles = d.get('roles') if isinstance(d.get('roles'), dict) else {}
    ok_keys = ['greet', 'right', 'wrong'] + [u + l for u in ('u1', 'u2') for l in 'ABCD']
    safe = {k: (str(roles.get(k))[:8] if re.fullmatch(r'[a-z0-9-]{1,8}', str(roles.get(k) or '')) else None) for k in ok_keys}
    c.execute("REPLACE INTO kv VALUES ('roles', ?)", (json.dumps(safe),))
    c.commit(); c.close()
    return jsonify(ok=True)


# ---------- right / wrong answer sounds uploaded from the host dashboard ----------
@app.route('/sound/<k>', methods=['GET'])
def sound_get(k):
    if k not in SOUND_KEYS:
        return 'not found', 404
    c = db()
    r = c.execute('SELECT value FROM kv WHERE key=?', ('sound_' + k,)).fetchone()
    c.close()
    if not r:
        return 'not found', 404
    mime, data = json.loads(r[0])
    return Response(base64.b64decode(data), mimetype=mime)


@app.route('/sound', methods=['POST', 'OPTIONS'])
def sound_set():
    if request.method == 'OPTIONS':
        return '', 204
    d = body()
    if d is None or not host_ok(d.get('id', ''), d.get('pass', '')):
        return jsonify(ok=False, error='Wrong ID or password')
    k, url = d.get('k'), str(d.get('data', ''))
    if k not in SOUND_KEYS or not url.startswith('data:audio') or ',' not in url or len(url) > 3000000:
        return jsonify(ok=False, error='That is not a usable sound file')
    head, data = url.split(',', 1)
    mime = head[5:].split(';')[0]
    c = db()
    c.execute('REPLACE INTO kv VALUES (?, ?)', ('sound_' + k, json.dumps([mime, data])))
    c.commit(); c.close()
    return jsonify(ok=True)


# ---------- invite-code rooms ----------
def load_room(c, code):
    r = c.execute('SELECT state FROM rooms WHERE code=?', (code,)).fetchone()
    return json.loads(r[0]) if r else None


def save_room(c, s):
    c.execute('REPLACE INTO rooms VALUES (?,?,?)', (s['code'], json.dumps(s), time.time()))
    c.commit()


def public_room(s):
    keys = ('code', 'names', 'ch', 'hc', 'hue', 'mode', 'mid', 'qi', 'total', 'score', 'right', 'wrong',
            'locked', 'over', 'done', 'ev', 'last')
    p = {k: s[k] for k in keys}
    cur = s['qs'][s['qi']]
    p['sg'] = s.get('sg', {})          # voice-chat signals (one slot per player)
    p['q'] = {'q': cur['q'], 'o': cur['o']}
    if s['over']:
        p['q']['a'] = cur['a']      # the answer is only sent once the question is over
    return p


@app.route('/room', methods=['GET', 'POST', 'OPTIONS'], strict_slashes=False)
def room():
    if request.method == 'OPTIONS':
        return '', 204
    c = db()
    if request.method == 'GET':
        s = load_room(c, request.args.get('code', '').upper())
        c.close()
        return jsonify(ok=True, room=public_room(s)) if s else jsonify(ok=False, error='Room not found')
    d = body()
    if d is None:
        c.close()
        return 'bad request', 400
    c.execute('BEGIN IMMEDIATE')
    act = d.get('action')
    code = str(d.get('code', '')).upper()

    if act == 'create':
        m = d.get('mode') or {}
        qs = [dict(q=str(x['q'])[:300], o=[str(o)[:120] for o in x['o'][:4]], a=num(x['a']))
              for x in (m.get('qs') or [])[:50]]
        if not qs:
            c.rollback(); c.close()
            return jsonify(ok=False, error='No questions in this mode')
        code = ''.join(random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(5))
        s = dict(code=code, names=[str(d.get('name', 'Player 1'))[:24], None],
                 ch=[str(d.get('ch0', 'volt'))[:20], None], hc=str(d.get('hc', 'rishi'))[:20],
                 mode=str(m.get('name', ''))[:40], mid=str(m.get('id', ''))[:40], hue=num(m.get('hue')), qs=qs, qi=0,
                 total=len(qs), score=[0, 0], right=[0, 0], wrong=[0, 0],
                 locked=[False, False], over=False, done=False, ev=0, last=None, sg={})
        save_room(c, s); c.close()
        return jsonify(ok=True, code=code)

    s = load_room(c, code)
    if not s:
        c.rollback(); c.close()
        return jsonify(ok=False, error='Room not found')

    if act == 'join':
        if s['names'][1]:
            c.rollback(); c.close()
            return jsonify(ok=False, error='This room is full')
        s['names'][1] = str(d.get('name', 'Player 2'))[:24]
        s['ch'][1] = str(d.get('ch1', 'shakti'))[:20]
        s['ev'] += 1
    elif act == 'answer' and s['names'][1] and not s['over'] and not s['done']:
        p = 1 if num(d.get('p')) == 1 else 0
        if not s['locked'][p]:
            ok = num(d.get('i')) == s['qs'][s['qi']]['a']   # first correct request to arrive wins
            s['ev'] += 1
            if ok:
                s['score'][p] += 20
                s['right'][p] += 1
                s['over'] = True
            else:
                s['score'][p] -= 10
                s['wrong'][p] += 1
                s['locked'][p] = True
                if all(s['locked']):
                    s['over'] = True
            s['last'] = dict(p=p, ok=ok, i=num(d.get('i')), n=s['ev'])
    elif act == 'sig' and s['names'][1]:
        s.setdefault('sg', {})[str(1 if num(d.get('p')) == 1 else 0)] = str(d.get('msg', ''))[:12000]
    elif act == 'next' and s['over'] and not s['done'] and num(d.get('qi')) == s['qi']:
        s['ev'] += 1
        if s['qi'] + 1 >= s['total']:
            s['done'] = True
        else:
            s['qi'] += 1
            s['over'] = False
            s['locked'] = [False, False]
    save_room(c, s); c.close()
    return jsonify(ok=True, last=s['last'])


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
