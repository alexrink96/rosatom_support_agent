
from datetime import datetime, timedelta, timezone
from flask import Flask, session, request, jsonify, render_template_string, g, redirect
import os
import sqlite3
import requests
import json


MSK = timezone(timedelta(hours=3))  
DB_PATH = 'support.db'

app = Flask(__name__)
app.secret_key = 'super-secret-key'
app.config['JSON_AS_ASCII'] = False

def add_to_session_history(role, text):
    if 'chat_history' not in session:
        session['chat_history'] = []
    now = datetime.now(MSK).strftime("%H:%M")
    session['chat_history'].append({'role': role, 'text': text, 'time': now})
    session.modified = True

# ---------------------- DB helpers ----------------------

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # create faq
    c.execute('''CREATE TABLE IF NOT EXISTS faq (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        question TEXT,
        answer TEXT,
        keywords TEXT
    )''')
    # create tickets
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_msg TEXT,
        category TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'open',
        operator_answer TEXT
    )''')
    conn.commit()

    # seed if empty
    c.execute('SELECT COUNT(*) FROM faq')
    if c.fetchone()[0] == 0:
        sample = [
            ('Пароль', 'Как восстановить пароль?', 'Чтобы восстановить пароль, перейдите по ссылке /reset и следуйте инструкциям.', 'пароль;восстановить;забыл;вход'),
            ('Доступ', 'Не могу войти в систему', 'Проверьте логин и пароль. Если не помогает, нажмите "Забыли пароль".', 'войти;не могу;авторизоваться;логин'),
            ('Отчет', 'Ошибка при выгрузке отчёта', 'Попробуйте очистить кэш и повторить. Если проблема останется, передайте обращение оператору.', 'отчёт;выгрузка;ошибка'),
            ('VPN', 'Не удаётся подключиться к VPN', 'Проверьте интернет-соединение и актуальность пароля. Попробуйте перезапустить клиент VPN.', 'vpn;подключение;ошибка;сеть'),
            ('Принтер', 'Не печатает принтер', 'Проверьте, выбран ли нужный принтер по умолчанию и есть ли бумага. При необходимости перезапустите устройство.', 'принтер;печать;бумага;устройство'),
            ('Антивирус', 'Как обновить антивирус', 'Антивирус обновляется автоматически. Если обновления не устанавливаются, перезагрузите компьютер.', 'антивирус;обновление;безопасность'),
            ('Обновления', 'Компьютер требует обновление системы', 'Разрешите установку обновлений в нерабочее время. Это поможет поддерживать безопасность системы.', 'обновление;windows;установка;система'),
            ('1С', 'Не открывается программа 1С', 'Закройте все запущенные процессы 1С и попробуйте снова. Если ошибка повторяется, обратитесь в техподдержку.', '1с;не открывается;ошибка;учёт'),
            ('Outlook', 'Как добавить подпись в письмах Outlook', 'Откройте «Файл → Параметры → Почта → Подписи» и создайте новую подпись.', 'outlook;подпись;письмо;почта'),
        ]
        c.executemany('INSERT INTO faq (category, question, answer, keywords) VALUES (?, ?, ?, ?)', sample)
        conn.commit()
    conn.close()

# ---------------------- Classifier ----------------------

def process_llm_answer(llm_answer):
    categories = get_categories()
    for category in categories:
        if category in llm_answer:
            return category
    return "Другое"

def classify_with_llm(text):
    LLM_API_URL = "http://fastapi_model:8000/classify"

    categories_from_db = f"Список категорий:\n{get_categories_formatted()}\n- Другое"
  
    PROMPT_TEMPLATE = """
    Ты — агент техподдержки.
    Твоя задача: определить к какой из следующих категорий относится пользовательское обращение. Дай ответ коротко (только название категории).
    """

    prompt = f"{PROMPT_TEMPLATE}\n{categories_from_db}\nСообщение от пользователя: {text}"

    try:
      resp = requests.post(LLM_API_URL, json={"prompt": prompt})

    except Exception:
      return None
    else:
      if resp.ok:
          category_by_llm = resp.text
          category_by_llm_processed = process_llm_answer(category_by_llm)
          return {'category': category_by_llm_processed, 'confidence': 0.6}
      else:
          return None
        

def simple_keyword_classify(text):
    t = text.lower()
    if any(k in t for k in ['пароль', 'войти', 'логин', 'авториз']):
        return {'category': 'Доступ', 'confidence': 0.6}
    if any(k in t for k in ['отчёт', 'отчет', 'выгруз', 'excel']):
        return {'category': 'Отчёты', 'confidence': 0.6}
    if any(k in t for k in ['ошибка', 'не работает', 'сбой']):
        return {'category': 'Ошибка', 'confidence': 0.5}
    return {'category': 'Неизвестно', 'confidence': 0.3}

def classify_text(text):
    res = classify_with_llm(text)
    if res:
        return res
    return simple_keyword_classify(text)


def find_faq_by_category(category):
    db = get_db()
    c = db.cursor()
    c.execute('SELECT * FROM faq WHERE category = ? ORDER BY id LIMIT 1', (category,))
    return c.fetchone()

def find_faq_by_keyword_match(text):
    db = get_db()
    c = db.cursor()
    c.execute('SELECT * FROM faq')
    rows = c.fetchall()
    t = text.lower()
    for r in rows:
        if r['keywords']:
            for kw in r['keywords'].split(';'):
                if kw.strip() and kw.strip() in t:
                    return r
    return None
    
    
def get_categories():
    """Возвращает все уникальные категории в отформатированном виде"""
    db = get_db()
    c = db.cursor()
    
    # Получаем уникальные категории
    c.execute('SELECT DISTINCT category FROM faq WHERE category IS NOT NULL ORDER BY category')
    
    # Извлекаем все категории
    categories = [row[0] for row in c.fetchall()]
    return categories
    

def get_categories_formatted():
    # Извлекаем все категории
    categories = get_categories()
    
    # Форматируем в нужный вид
    if categories:
        formatted_categories = "\n".join([f"- {category}" for category in categories])
        return formatted_categories
    else:
        return "Категории не найдены"

# ---------------------- HTML templates ----------------------

INDEX_HTML = '''
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Прототип техподдержки — ROSATOM</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    #chat { height: 60vh; overflow-y: auto; }
    .msg { margin: 8px 0; }
    .user { text-align: right; }
    .bot { text-align: left; }
  </style>
</head>
<body class="bg-light">
<div class="container py-4">
  <h2 class="mb-3">Чат поддержки (прототип)</h2>
  <div id="chat" class="border rounded bg-white p-3 mb-3"></div>
    <div class="input-group mt-2">
      <input id="input" class="form-control" placeholder="Напишите сообщение..." 
             onkeydown="if(event.key==='Enter'){ event.preventDefault(); send(); }">
      <button class="btn btn-primary" onclick="send()">Отправить</button>
    </div>
  <p class="mt-3">Оператор: <a href="/operator" target="_blank">открыть панель оператора</a></p>
</div>
<script>
  let pendingTickets = {};

  // Подгрузка истории чата при загрузке страницы
  const history = {{ history|tojson }};

  history.forEach(msg => {
    let role = msg.role === 'user' ? 'user' 
            : msg.role === 'support' ? 'bot' 
            : 'operator';
    append(role, msg.text, msg.time);
  });

  function append(role, text, time){
    const d = document.createElement('div');
    d.className = 'msg ' + (role==='user' ? 'user text-end' : role==='bot' ? 'bot text-start' : 'op text-start');
    let label = role==='user' ? 'Вы' : role==='bot' ? 'Система' : 'Оператор';
    d.innerHTML = `
      <div>
        <span class="badge bg-${role==='user' ? 'primary' : role==='bot' ? 'secondary' : 'success'}">${label}</span>
        ${text}
      </div>
      <div class="text-muted small mt-1">${time || ''}</div>
    `;
    document.getElementById('chat').appendChild(d);
    document.getElementById('chat').scrollTop = document.getElementById('chat').scrollHeight;
  }

  function send(){
    const t = document.getElementById('input').value;
    if(!t) return;
    append('user', t, new Date().toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'}));
    document.getElementById('input').value = '';
    fetch('/api/message', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({message:t})
    })
    .then(r=>r.json())
    .then(j=>{
      append('bot', j.reply, new Date().toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'}));
      if (j.ticket_id){
        pendingTickets[j.ticket_id] = true;
      }
    });
  }

  // периодический опрос ответов оператора
  setInterval(()=>{
    Object.keys(pendingTickets).forEach(id=>{
      fetch('/api/tickets/'+id).then(r=>r.json()).then(j=>{
        if(j.ticket && j.ticket.status==='closed' && j.ticket.operator_answer){
          append('operator', j.ticket.operator_answer, new Date().toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'}));

          // Сохраняем ответ в сессию
          fetch('/api/add_to_history', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({role:'operator', text: j.ticket.operator_answer})
          });

          delete pendingTickets[id];
        }
      })
    })
  }, 5000);
</script>
</body>
</html>
'''

OPERATOR_HTML = '''
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Оператор</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
<div class="container py-4">
  <h2>Панель оператора</h2>
  <h3 class="mt-4">Открытые тикеты</h3>
  <div id="tickets" class="my-3">Загрузка...</div>
  <hr>
  <h3>Добавить/редактировать FAQ</h3>
  <form id="faqform" class="mt-3" onsubmit="event.preventDefault(); saveFaq();">
    <input id="faq_id" type="hidden" />
    <div class="mb-2">
      <label class="form-label">Категория</label>
      <input id="faq_cat" class="form-control" />
    </div>
    <div class="mb-2">
      <label class="form-label">Вопрос</label>
      <input id="faq_q" class="form-control" />
    </div>
    <div class="mb-2">
      <label class="form-label">Ответ</label>
      <input id="faq_a" class="form-control" />
    </div>
    <div class="mb-2">
      <label class="form-label">Ключи (через ;)</label>
      <input id="faq_k" class="form-control" />
    </div>
    <button class="btn btn-success">Сохранить FAQ</button>
  </form>
</div>
<script>
    let drafts = {};

    function loadTickets(){
      // сохраняем черновики
      document.querySelectorAll("textarea[id^='ans_']").forEach(el=>{
        drafts[el.id] = el.value;
      });

      // сохраняем фокус
      let focusedId = document.activeElement ? document.activeElement.id : null;
      let cursorPos = null;
      if (focusedId && focusedId.startsWith("ans_")) {
        cursorPos = document.activeElement.selectionStart;
      }

      fetch('/api/tickets').then(r=>r.json()).then(j=>{
        const el = document.getElementById('tickets');
        el.innerHTML = '';
        if(j.tickets.length===0){
          el.innerHTML = '<div class="text-muted fst-italic">Нет открытых тикетов</div>';
        }
        j.tickets.forEach(t=>{
          const d = document.createElement('div');
          d.className = 'card mb-3';
          d.innerHTML = `
            <div class="card-body">
              <h5 class="card-title">№${t.id} <small class="text-muted">(${t.created_at})</small></h5>
              <p><b>Сообщение:</b> ${t.user_msg}</p>
              <p><b>Категория:</b> ${t.category}</p>
              <textarea id='ans_${t.id}' class='form-control mb-2' placeholder='Ответ оператора'></textarea>
              <button class='btn btn-primary btn-sm me-2' onclick='sendAnswer(${t.id})'>Отправить и закрыть</button>
              <button class='btn btn-outline-secondary btn-sm' onclick='prefillFaq(${t.id})'>Добавить как FAQ</button>
            </div>`;
          el.appendChild(d);

          // восстановим текст если был
          const textarea = document.getElementById('ans_'+t.id);
          if(drafts['ans_'+t.id]){
            textarea.value = drafts['ans_'+t.id];
          }
        });

        // восстанавливаем фокус
        if (focusedId && document.getElementById(focusedId)) {
          const el = document.getElementById(focusedId);
          el.focus();
          if (cursorPos !== null) {
            el.setSelectionRange(cursorPos, cursorPos);
          }
        }
      })
    }
  
  function sendAnswer(id){
    const text = document.getElementById('ans_'+id).value;
    fetch('/api/tickets/'+id+'/answer', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({answer:text})}).then(()=>loadTickets())
  }
  function prefillFaq(id){
    fetch('/api/tickets/'+id).then(r=>r.json()).then(j=>{
      document.getElementById('faq_cat').value = j.ticket.category || '';
      document.getElementById('faq_q').value = j.ticket.user_msg.substring(0,80);
      document.getElementById('faq_a').value = '';
      document.getElementById('faq_k').value = '';
    })
  }
  function saveFaq(){
    const payload = {
      category: document.getElementById('faq_cat').value,
      question: document.getElementById('faq_q').value,
      answer: document.getElementById('faq_a').value,
      keywords: document.getElementById('faq_k').value
    };
    fetch('/api/faq', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)})
      .then(()=>{ alert('Сохранено'); document.getElementById('faqform').reset(); loadTickets(); })
  }
  loadTickets();
  setInterval(loadTickets, 5000);
</script>
</body>
</html>
'''

# ---------------------- Routes ----------------------

@app.route('/')
def index():
    if 'chat_history' not in session:
        session['chat_history'] = []
    return render_template_string(INDEX_HTML, history=session['chat_history'])

@app.route('/api/message', methods=['POST'])
def api_message():

    data = request.get_json() or {}
    text = data.get('message', '').strip()
    if not text:
        return jsonify({'reply': 'Пустое сообщение'}), 400
    
    add_to_session_history('user', text)

    cls = classify_text(text)
    category = cls.get('category') if isinstance(cls, dict) else cls
    faq = None
    if category and category != 'Неизвестно':
        faq = find_faq_by_category(category)
    if not faq:
        faq = find_faq_by_keyword_match(text)
    if faq:
        reply = f"[Из базы знаний — {faq['question']}]\n{faq['answer']}"
        add_to_session_history('support', reply)
        session.modified = True
        return jsonify({'reply': reply})
    db = get_db()
    c = db.cursor()
    c.execute('INSERT INTO tickets (user_msg, category) VALUES (?, ?)', (text, category))
    db.commit()
    ticket_id = c.lastrowid
    reply = f"Мы не нашли готового ответа — ваш запрос передан оператору (тикет #{ticket_id})."

    add_to_session_history('support', reply)
    session.modified = True

    return jsonify({'reply': reply, 'ticket_id': ticket_id})

@app.route('/operator')
def operator_ui():
    return render_template_string(OPERATOR_HTML)

@app.route('/api/tickets')
def api_tickets():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, user_msg, category, created_at FROM tickets WHERE status='open' ORDER BY id DESC")
    return jsonify({'tickets': [dict(r) for r in c.fetchall()]})

@app.route('/api/tickets/<int:ticket_id>')
def api_ticket(ticket_id):
    db = get_db()
    c = db.cursor()
    c.execute('SELECT * FROM tickets WHERE id=?', (ticket_id,))
    r = c.fetchone()
    if not r:
        return jsonify({'error':'not found'}), 404
    return jsonify({'ticket': dict(r)})

@app.route('/api/tickets/<int:ticket_id>/answer', methods=['POST'])
def api_ticket_answer(ticket_id):
    data = request.get_json() or {}
    ans = data.get('answer', '')
    db = get_db()
    c = db.cursor()
    c.execute('UPDATE tickets SET status=?, operator_answer=? WHERE id=?',
              ('closed', ans, ticket_id))
    db.commit()
    return jsonify({'ok': True, 'operator_answer': ans})

@app.route('/api/faq', methods=['POST'])
def api_faq_add():
    data = request.get_json() or {}
    db = get_db()
    c = db.cursor()
    c.execute('INSERT INTO faq (category, question, answer, keywords) VALUES (?, ?, ?, ?)',
              (data.get('category',''), data.get('question',''), data.get('answer',''), data.get('keywords','')))
    db.commit()
    return jsonify({'ok': True})

@app.route('/api/add_to_history', methods=['POST'])
def add_to_history():
    data = request.get_json() or {}
    role = data.get('role')
    text = data.get('text')
    if not role or not text:
        return jsonify({'error':'Нет роли или текста'}), 400

    add_to_session_history(role, text)

    return jsonify({'status':'ok'})


# ---------------------- Run ----------------------

if __name__ == '__main__':
    init_db()
    app.run(host="0.0.0.0", port=5000)
