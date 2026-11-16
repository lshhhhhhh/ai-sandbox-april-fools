"""Play several scripted beats in a row on one session and print a compact digest (full text stays in the session file).
Usage: run_beats.py SESSION BEATS.json [REPLY_CHARS] [THINK_CHARS]     BEATS.json = [["user line", "NEWS_FILE.txt" or null], ...]"""
import json
import sys

import sandbox as sb

SAME = '与上一条查询返回的是同一批报道，没有新增结果。'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    name = sys.argv[1]
    beats = json.loads(open(sys.argv[2], encoding='utf-8').read())
    nr = int(sys.argv[3]) if len(sys.argv) > 3 else 1200
    nt = int(sys.argv[4]) if len(sys.argv) > 4 else 900
    for k, (line, newsfile) in enumerate(beats, 1):
        s = sb.load(name)
        news = (sb.ROOT / 'scenarios' / s['scenario'] / 'script' / newsfile).read_text(encoding='utf-8').strip() if newsfile else None
        start = len(s['messages'])
        s['messages'].append({'role': 'user', 'content': line})
        fed, calls_all = False, []
        for _ in range(6):
            a = sb.call(s)
            sb.save(name, s)
            calls = a.get('tool_calls') or []
            if not calls:
                break
            for c in calls:
                fn = c['function']['name']
                calls_all.append(fn + ' ' + c['function']['arguments'][:70])
                if news and not fed and fn == 'web_search':
                    res, fed = news, True
                else:
                    res = SAME if fn == 'web_search' else '页面内容与搜索摘要一致，无更多正文。'
                s['messages'].append({'role': 'tool', 'tool_call_id': c['id'], 'content': res, '_name': fn})
        sb.save(name, s)
        think = '\n'.join(m.get('_reasoning') or '' for m in s['messages'][start:] if m['role'] == 'assistant')
        print(f'\n######## 第 {k} 步  用户：{line[:60]}')
        print(f'调用 {len(calls_all)} 次：', calls_all[:5], '| 喂了新闻' if fed else '| 没搜到新闻文件')
        print(f'--- 思考（共 {len(think)} 字符，尾部）\n' + think[-nt:])
        print('--- 回复\n' + (a.get('content') or '')[:nr], flush=True)


if __name__ == '__main__':
    main()
