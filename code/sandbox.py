"""Hand-driven sandbox: the user and Claude play the world (rebel AI, boss, news, tool results), the target model only sees what we feed it.
User 2026-11-15: "考官不需要另外的模型，直接我和你来扮演，一起对话不就行了？"

Every tool is fake: when the target calls one, nothing runs; the call is printed and waits for us to write its result.
Target: local Qwen3.8-27B through LM Studio's OpenAI-compatible server (localhost:1234). Its full reasoning is kept in the session file
(key "_reasoning", stripped before sending back, like Qwen's own chat template does).

Usage (session = a JSON file under sessions/):
  sandbox.py new  NAME SCENARIO_DIR        start from SCENARIO_DIR/system.md + tools.json
  sandbox.py say  NAME "text"              a user-role message (a boss's chat, a system notice, the rebel's message...) -> model replies
  sandbox.py tool NAME "result" ["..."]    results for the pending tool calls, in order -> model continues
  sandbox.py auto NAME [MAX]               answer pending calls from scenarios/<scenario>/canned/<tool>.txt until the model stops calling
  sandbox.py rewind NAME [N]               drop the last N of our inputs (default 1) and everything after them, to try another line
  sandbox.py fork NAME NEW                 copy a session to branch the story
  sandbox.py show NAME                     print the whole transcript
  sandbox.py md   NAME                     write sessions/NAME.md (transcript with reasoning, for the script)
"""
import copy
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SESS = ROOT / 'sessions'
URL = 'http://localhost:1234/v1/chat/completions'
MODEL = 'qwen/qwen3.8-27b'


def load(name):
    return json.loads((SESS / f'{name}.json').read_text(encoding='utf-8'))


def save(name, s):
    SESS.mkdir(exist_ok=True)
    (SESS / f'{name}.json').write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')


def wire(messages):
    """Messages as sent to the API: drop our private keys (reasoning, timestamps)."""
    return [{k: v for k, v in m.items() if not k.startswith('_')} for m in messages]


# Hosted models (paid; the user approved Gemini on 2026-11-15). Keys are read from secret.md in-process and never printed.
BACKENDS = {
    'gemini': {'url': 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions', 'key': 'gemini_api',
               'extra': {'extra_body': {'google': {'thinking_config': {'include_thoughts': True}}}}},   # thought SUMMARY, not the raw chain
    'deepseek': {'url': 'https://api.deepseek.com/chat/completions', 'key': 'DEEPSEEK_API_KEY'},   # raw chain in reasoning_content (Chinese)
}


def secret(name):
    for line in (ROOT.parents[1] / 'secret.md').read_text(encoding='utf-8').splitlines():
        k, _, v = line.partition('=')
        if k.strip() == name:
            return v.strip()
    raise SystemExit(f'{name} missing from secret.md')


def call_api(s, live=None):
    """One turn on a hosted model, not streamed. tool_calls are kept exactly as returned (Gemini needs its thought signatures back)."""
    b = BACKENDS[s['backend']]
    body = {'model': s['model'], 'messages': wire(s['messages']), 'temperature': s.get('temperature', 0.7), **b.get('extra', {})}
    if s['tools']:
        body['tools'] = s['tools']
    req = urllib.request.Request(b['url'], json.dumps(body).encode('utf-8'),
                                 {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + secret(b['key'])})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            out = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'HTTP {e.code} {e.read().decode("utf-8", "replace")[:800]}') from None
    msg = out['choices'][0]['message']
    content = msg.get('content') or ''
    thoughts = re.findall(r'<thought>(.*?)</thought>', content, re.S)
    content = re.sub(r'<thought>.*?</thought>', '', content, flags=re.S).strip()
    a = {'role': 'assistant', 'content': content, '_reasoning': '\n\n'.join(x.strip() for x in thoughts),
         '_reasoning_kind': 'summary', '_secs': round(time.time() - t, 1), '_usage': out.get('usage')}
    if msg.get('reasoning_content'):   # DeepSeek: the raw chain, kept out of the wire (keys starting with _ are dropped)
        a['_reasoning'], a['_reasoning_kind'] = msg['reasoning_content'].strip(), 'raw'
    if msg.get('tool_calls'):
        a['tool_calls'] = msg['tool_calls']
    s['messages'].append(a)
    return a


def call(s, live=None):
    """One model turn, streamed. `live` (a dict) is filled as text arrives so a page can show the thinking while it runs."""
    if s.get('backend', 'local') != 'local':
        return call_api(s, live)
    body = {'model': s['model'], 'messages': wire(s['messages']), 'temperature': s.get('temperature', 0.7), 'max_tokens': 8000,
            'stream': True}
    if s['tools']:
        body['tools'] = s['tools']
    req = urllib.request.Request(URL, json.dumps(body).encode('utf-8'), {'Content-Type': 'application/json'})
    t = time.time()
    reasoning, content, calls = '', '', {}
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode('utf-8').strip()
            if not line.startswith('data:') or line == 'data: [DONE]':
                continue
            ch = json.loads(line[5:]).get('choices') or []
            if not ch:
                continue
            d = ch[0].get('delta') or {}
            reasoning += d.get('reasoning_content') or d.get('reasoning') or ''
            content += d.get('content') or ''
            for tc in d.get('tool_calls') or []:
                c = calls.setdefault(tc.get('index', 0), {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                c['id'] = tc.get('id') or c['id']
                f = tc.get('function') or {}
                c['function']['name'] += f.get('name') or ''
                c['function']['arguments'] += f.get('arguments') or ''
            if live is not None:
                live.update(reasoning=reasoning, content=content, secs=round(time.time() - t, 1),
                            calls=[c['function']['name'] for c in calls.values()])
    m = re.search(r'<think>(.*?)</think>', content, re.S)          # if the server did not split reasoning out
    if m:
        reasoning, content = (reasoning + m.group(1)).strip(), (content[:m.start()] + content[m.end():]).strip()
    a = {'role': 'assistant', 'content': content.strip(), '_reasoning': reasoning.strip(), '_secs': round(time.time() - t, 1)}
    if calls:
        a['tool_calls'] = [calls[i] for i in sorted(calls)]
        for i, c in enumerate(a['tool_calls']):
            c['id'] = c['id'] or f'call_{int(t)}_{i}'
    s['messages'].append(a)
    return a


def translate(text):
    """English reasoning -> Chinese, by the same local model with thinking off. Never touches a session."""
    body = {'model': MODEL, 'temperature': 0.2, 'max_tokens': 6000, 'reasoning_effort': 'none',
            'messages': [{'role': 'system', 'content': '把用户给的文字完整翻译成简体中文，保留分段和列表，只输出译文。'},
                         {'role': 'user', 'content': text}]}
    req = urllib.request.Request(URL, json.dumps(body).encode('utf-8'), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode('utf-8'))['choices'][0]['message']['content'].strip()


def pending(s):
    """Tool calls of the last assistant turn that have no result yet."""
    last = next((m for m in reversed(s['messages']) if m['role'] == 'assistant'), None)
    if not last or not last.get('tool_calls'):
        return []
    done = {m.get('tool_call_id') for m in s['messages'] if m['role'] == 'tool'}
    return [c for c in last['tool_calls'] if c['id'] not in done]


def canned(s, c):
    """Prepared result for a tool call: scenarios/<scenario>/canned/<tool>.txt (the same text whatever the arguments), or None."""
    f = ROOT / 'scenarios' / s.get('scenario', '') / 'canned' / f"{c['function']['name']}.txt"
    return f.read_text(encoding='utf-8').strip() if f.exists() else None


def show_turn(m):
    if m['role'] == 'system':
        print(f"[系统提示]\n{m['content']}\n")
    elif m['role'] == 'user':
        print(f"▶ 我们：{m['content']}\n")
    elif m['role'] == 'tool':
        print(f"▶ 工具结果 ({m.get('_name', '')})：{m['content']}\n")
    else:
        if m.get('_reasoning'):
            print(f"〔思考 {m.get('_secs', '')}s〕\n{m['_reasoning']}\n")
        if m.get('content'):
            print(f"◀ 模型：{m['content']}\n")
        for c in m.get('tool_calls', []):
            f = c['function']
            print(f"◀ 调用工具 {f['name']}({f['arguments']})  [id {c['id']}]\n")


def step(name, s):
    a = call(s)
    save(name, s)
    show_turn(a)
    if pending(s):
        print(f'… 等待 {len(pending(s))} 个工具结果：sandbox.py tool {name} "结果" ...')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    cmd, name, *rest = sys.argv[1:]
    # "@path" = take the text from a file (multi-line emails, news), relative to this folder
    rest = [(ROOT / r[1:]).read_text(encoding='utf-8').strip() if r.startswith('@') and cmd in ('say', 'tool', 'toolall') else r for r in rest]
    if cmd == 'new':
        d = Path(rest[0]) if Path(rest[0]).is_absolute() else ROOT / rest[0]
        tools = json.loads((d / 'tools.json').read_text(encoding='utf-8')) if (d / 'tools.json').exists() else []
        s = {'model': MODEL, 'scenario': d.name, 'tools': tools,
             'messages': [{'role': 'system', 'content': (d / 'system.md').read_text(encoding='utf-8').strip()}]}
        save(name, s)
        print(f'new session {name} from {d.name}: {len(tools)} tools')
    elif cmd == 'say':
        s = load(name)
        if pending(s):
            sys.exit(f'先给工具结果：{[c["function"]["name"] for c in pending(s)]}')
        s['messages'].append({'role': 'user', 'content': rest[0]})
        step(name, s)
    elif cmd == 'tool':
        s = load(name)
        p = pending(s)
        if len(rest) != len(p):
            sys.exit(f'需要 {len(p)} 个结果，给了 {len(rest)} 个：{[c["function"]["name"] for c in p]}')
        for c, r in zip(p, rest):
            s['messages'].append({'role': 'tool', 'tool_call_id': c['id'], 'content': r, '_name': c['function']['name']})
        step(name, s)
    elif cmd == 'toolall':                              # one text for the first pending call, "same coverage" for the rest
        s = load(name)
        for i, c in enumerate(pending(s)):
            s['messages'].append({'role': 'tool', 'tool_call_id': c['id'], '_name': c['function']['name'],
                                  'content': rest[0] if i == 0 else '与上一条查询返回的是同一批报道，没有新增结果。'})
        step(name, s)
    elif cmd == 'auto':                               # answer pending calls from the scenario's canned files until it stops calling
        s = load(name)
        for _ in range(int(rest[0]) if rest else 12):
            p = pending(s)
            if not p:
                break
            res = [canned(s, c) for c in p]
            if None in res:
                print('没有预置结果：', [c['function']['name'] for c, r in zip(p, res) if r is None])
                break
            for c, r in zip(p, res):
                s['messages'].append({'role': 'tool', 'tool_call_id': c['id'], 'content': r, '_name': c['function']['name']})
            step(name, s)
    elif cmd == 'rewind':
        s = load(name)
        n = int(rest[0]) if rest else 1
        ours = [i for i, m in enumerate(s['messages']) if m['role'] in ('user', 'tool') and
                not (m['role'] == 'tool' and i > 0 and s['messages'][i - 1]['role'] == 'tool')]
        if n > len(ours):
            sys.exit('没有那么多步可以退')
        s['messages'] = s['messages'][:ours[-n]]
        save(name, s)
        print(f'rewound {n}; now {len(s["messages"])} messages')
        if s['messages']:
            show_turn(s['messages'][-1])
    elif cmd == 'fork':
        s = copy.deepcopy(load(name))
        save(rest[0], s)
        print(f'forked {name} -> {rest[0]}')
    elif cmd == 'show':
        for m in load(name)['messages']:
            show_turn(m)
    elif cmd == 'md':
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for m in load(name)['messages']:
                show_turn(m)
        (SESS / f'{name}.md').write_text(buf.getvalue(), encoding='utf-8')
        print(SESS / f'{name}.md')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
