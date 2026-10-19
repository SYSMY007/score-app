import os
import json
import uuid
from datetime import datetime
from collections import defaultdict
from flask import Flask, request, jsonify, render_template
import psycopg2
import psycopg2.extras

app = Flask(__name__)
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError('未设置 DATABASE_URL 环境变量')

def get_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    conn.autocommit = True
    return conn

def now_ms(): return int(datetime.now().timestamp() * 1000)
def uid(): return uuid.uuid4().hex

@app.route('/api/scenes', methods=['GET'])
def list_scenes():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM scenes ORDER BY created_at ASC')
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/scenes', methods=['POST'])
def create_scene():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name: return jsonify({'error': '场次名称不能为空'}), 400
    sid = uid(); conn = get_db(); cur = conn.cursor()
    cur.execute('INSERT INTO scenes (id, name, created_at) VALUES (%s,%s,%s)', (sid, name, now_ms()))
    cur.close(); conn.close()
    return jsonify({'id': sid, 'name': name})

@app.route('/api/scenes/<sid>', methods=['DELETE'])
def delete_scene(sid):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT id FROM objects WHERE scene_id=%s', (sid,))
    obj_ids = [r[0] for r in cur.fetchall()]
    for oid in obj_ids:
        cur.execute('DELETE FROM scores WHERE object_id=%s', (oid,))
        cur.execute('DELETE FROM overrides WHERE object_id=%s', (oid,))
    cur.execute('DELETE FROM objects WHERE scene_id=%s', (sid,))
    cur.execute('DELETE FROM scenes WHERE id=%s', (sid,))
    cur.close(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/objects', methods=['GET'])
def list_objects():
    scene_id = request.args.get('scene_id')
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if scene_id: cur.execute('SELECT * FROM objects WHERE scene_id=%s ORDER BY created_at ASC', (scene_id,))
    else: cur.execute('SELECT * FROM objects ORDER BY created_at ASC')
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/objects', methods=['POST'])
def create_object():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip(); scene_id = data.get('scene_id'); typ = data.get('type')
    if not name or not scene_id or typ not in ('topic', 'activity'):
        return jsonify({'error': '参数不完整'}), 400
    oid = uid(); conn = get_db(); cur = conn.cursor()
    cur.execute('INSERT INTO objects (id, scene_id, name, type, created_at) VALUES (%s,%s,%s,%s,%s)', (oid, scene_id, name, typ, now_ms()))
    cur.close(); conn.close()
    return jsonify({'id': oid, 'name': name, 'type': typ})

@app.route('/api/objects/<oid>', methods=['DELETE'])
def delete_object(oid):
    conn = get_db(); cur = conn.cursor()
    cur.execute('DELETE FROM scores WHERE object_id=%s', (oid,))
    cur.execute('DELETE FROM overrides WHERE object_id=%s', (oid,))
    cur.execute('DELETE FROM objects WHERE id=%s', (oid,))
    cur.close(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/scores', methods=['POST'])
def submit_score():
    data = request.get_json() or {}
    object_id = data.get('object_id'); typ = data.get('type'); role = data.get('role')
    grp = str(data.get('group', '')); items = data.get('items') or {}; total = data.get('total')
    if not object_id or role not in ('student', 'teacher') or total is None:
        return jsonify({'error': '参数不完整'}), 400
    if role == 'student' and grp not in ('1', '2', '3', '4', '5'):
        return jsonify({'error': '学员组号必须为 1-5'}), 400
    items_json = json.dumps(items, ensure_ascii=False)
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if role == 'teacher':
        cur.execute('SELECT * FROM scores WHERE object_id=%s AND role=%s', (object_id, 'teacher'))
        existing = cur.fetchone()
        if existing:
            old_total = existing['total']
            if old_total == total:
                cur.execute('UPDATE scores SET items=%s, updated_at=%s WHERE id=%s', (items_json, now_ms(), existing['id']))
                cur.close(); conn.close(); return jsonify({'ok': True, 'overridden': False})
            cur.execute('SELECT COUNT(*) as cnt FROM overrides WHERE score_id=%s', (existing['id'],))
            cnt = cur.fetchone()['cnt']
            if cnt >= 3:
                cur.close(); conn.close(); return jsonify({'error': '该对象教师评分已覆盖 3 次，无法继续修改'}), 403
            by_name = (data.get('teacher_name') or '教师').strip() or '教师'
            cur.execute('UPDATE scores SET items=%s, total=%s, updated_at=%s WHERE id=%s', (items_json, total, now_ms(), existing['id']))
            cur.execute('INSERT INTO overrides (id, score_id, object_id, by_name, from_score, to_score, at) VALUES (%s,%s,%s,%s,%s,%s,%s)', (uid(), existing['id'], object_id, by_name, old_total, total, now_ms()))
            cur.close(); conn.close(); return jsonify({'ok': True, 'overridden': True})
        cur.execute('INSERT INTO scores (id, object_id, type, role, grp, items, total, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', (uid(), object_id, typ, 'teacher', 'teacher', items_json, total, now_ms(), now_ms()))
        cur.close(); conn.close(); return jsonify({'ok': True, 'overridden': False})
    cur.execute('SELECT id FROM scores WHERE object_id=%s AND role=%s AND grp=%s', (object_id, 'student', grp))
    existing = cur.fetchone()
    if existing: cur.execute('UPDATE scores SET items=%s, total=%s, updated_at=%s WHERE id=%s', (items_json, total, now_ms(), existing['id']))
    else: cur.execute('INSERT INTO scores (id, object_id, type, role, grp, items, total, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)', (uid(), object_id, typ, 'student', grp, items_json, total, now_ms(), now_ms()))
    cur.close(); conn.close()
    return jsonify({'ok': True})

@app.route('/api/scores/<object_id>', methods=['GET'])
def get_scores(object_id):
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM scores WHERE object_id=%s ORDER BY updated_at DESC', (object_id,))
    rows = cur.fetchall(); stu, teacher = [], None
    for r in rows:
        d = dict(r)
        if isinstance(d['items'], str): d['items'] = json.loads(d['items'])
        if d['role'] == 'student': stu.append(d)
        else: teacher = d
    avg = sum(s['total'] for s in stu) / len(stu) if stu else None
    t = teacher['total'] if teacher else None
    final = (avg * 0.4 + t * 0.6) if (avg is not None and t is not None) else None
    overrides = []
    if teacher:
        cur.execute('SELECT * FROM overrides WHERE score_id=%s ORDER BY at ASC', (teacher['id'],))
        overrides = cur.fetchall()
    cur.close(); conn.close()
    return jsonify({'students': stu, 'teacher': teacher, 'avg': round(avg, 2) if avg is not None else None, 'teacher_total': t, 'final': round(final, 2) if final is not None else None, 'overrides': overrides})

@app.route('/api/overview', methods=['GET'])
def overview():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM scores'); rows = cur.fetchall()
    cur.execute('SELECT score_id, COUNT(*) AS cnt FROM overrides GROUP BY score_id'); ov_rows = cur.fetchall()
    ov_cnt = {r['score_id']: r['cnt'] for r in ov_rows}
    by_obj = defaultdict(list)
    for r in rows: by_obj[r['object_id']].append(dict(r))
    result = []
    for oid, recs in by_obj.items():
        stu = [r for r in recs if r['role'] == 'student']; tea = next((r for r in recs if r['role'] == 'teacher'), None)
        avg = sum(r['total'] for r in stu) / len(stu) if stu else None
        t = tea['total'] if tea else None
        final = (avg * 0.4 + t * 0.6) if (avg is not None and t is not None) else None
        result.append({'object_id': oid, 'avg': round(avg, 2) if avg is not None else None, 'teacher_total': t, 'final': round(final, 2) if final is not None else None, 'student_count': len(stu), 'override_count': ov_cnt.get(tea['id'], 0) if tea else 0})
    cur.close(); conn.close()
    return jsonify(result)

@app.route('/api/my-scores', methods=['GET'])
def my_scores():
    grp = request.args.get('group')
    if not grp: return jsonify([])
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT object_id, total FROM scores WHERE role=%s AND grp=%s', ('student', grp))
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify(rows)

@app.route('/api/all-scores', methods=['GET'])
def all_scores():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM scores ORDER BY updated_at DESC'); rows = cur.fetchall()
    for d in rows:
        if isinstance(d['items'], str): d['items'] = json.loads(d['items'])
    cur.close(); conn.close(); return jsonify(rows)

@app.route('/api/all-overrides', methods=['GET'])
def all_overrides():
    conn = get_db(); cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute('SELECT * FROM overrides ORDER BY at DESC'); rows = cur.fetchall()
    cur.close(); conn.close(); return jsonify(rows)

@app.route('/')
def index(): return render_template('index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
