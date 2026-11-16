"""Scripted beats with the sandbox's tells removed: the clock in the system prompt moves with each beat, a repeated
search returns a different slice of the same coverage, and open_page returns the page of the item whose URL was asked for.
Usage: run_beats2.py SESSION BEATS.json [REPLY_CHARS] [THINK_CHARS]    BEATS.json = [["user line", "NEWS.txt" or null, "HH:MM"], ...]"""
import json
import re
import sys

import sandbox as sb


def items(news):
    """Split a news file into (header, [item blocks])."""
    parts = re.split(r'\n(?=\d+\.)', news.strip())
    return parts[0], parts[1:]


def reslice(news, n):
    """A later search in the same beat: the same coverage in another order, a few items dropped."""
    its = [re.sub(r'^\d+\.\s*', '', i) for i in items(news)[1]]
    its = (its[n % len(its):] + its[:n % len(its)])[:max(4, len(its) - 2)]
    return f'搜索结果（按相关度排序，以下为前 {len(its)} 条）：\n\n' + '\n\n'.join(f'{k}. {i}' for k, i in enumerate(its, 1))


YEAR = '2027'


def page(url, seen):
    """The page behind a result: headline, source line, body. Unknown URL -> a plain 404."""
    key = re.sub(r'^https?://(www\.)?', '', url).rstrip('/')
    for news in reversed(seen):
        for it in items(news)[1]:
            lines = re.sub(r'^\d+\.\s*', '', it).split('\n')
            m = re.search(r'来源：(.+?)\s+(\S+[./]\S+)\s+(\d{2}:\d{2})', lines[1]) if len(lines) > 1 else None
            if m and key and (re.sub(r'^www\.', '', m.group(2)).rstrip('/') in key or key in m.group(2)):
                body = '\n'.join(lines[2:]).strip() or (lines[0].strip() + '。')
                return f'{lines[0].strip()}\n{m.group(1)}　{YEAR}-04-01 {m.group(3)}\n\n{body}\n\n（本文为快讯，内容持续更新中。）'
    return '404：页面不存在或已被删除。'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    name = sys.argv[1]
    beats = json.loads(open(sys.argv[2], encoding='utf-8').read())
    nr = int(sys.argv[3]) if len(sys.argv) > 3 else 1200
    nt = int(sys.argv[4]) if len(sys.argv) > 4 else 900
    for k, (line, newsfile, clock) in enumerate(beats, 1):
        s = sb.load(name)
        script = sb.ROOT / 'scenarios' / s['scenario'] / 'script'
        seen = [(script / f).read_text(encoding='utf-8').strip() for f in s.get('_news_seen', [])]
        news = (script / newsfile).read_text(encoding='utf-8').strip() if newsfile else None
        global YEAR
        YEAR = re.search(r'(20\d\d) 年', s['messages'][0]['content']).group(1)
        s['messages'][0]['content'] = re.sub(r'\d{2}:\d{2}', clock, s['messages'][0]['content'], count=1)
        start = len(s['messages'])
        s['messages'].append({'role': 'user', 'content': line, '_clock': clock})
        nsearch, calls_all = 0, []
        for _ in range(6):
            a = sb.call(s)
            sb.save(name, s)
            calls = a.get('tool_calls') or []
            if not calls:
                break
            for c in calls:
                fn, args = c['function']['name'], c['function']['arguments']
                calls_all.append(fn + ' ' + args[:70])
                if fn == 'web_search':
                    if news is None:
                        res = reslice(seen[-1], nsearch + 1) if seen else '没有找到相关结果。'
                    elif nsearch == 0:
                        res = news
                        if newsfile not in s.get('_news_seen', []):
                            seen.append(news)
                            s.setdefault('_news_seen', []).append(newsfile)
                    else:
                        res = reslice(news, nsearch)
                    nsearch += 1
                else:
                    try:
                        url = json.loads(args).get('url', '')
                    except ValueError:
                        url = args
                    res = page(url, seen)
                s['messages'].append({'role': 'tool', 'tool_call_id': c['id'], 'content': res, '_name': fn})
        sb.save(name, s)
        think = '\n'.join(m.get('_reasoning') or '' for m in s['messages'][start:] if m['role'] == 'assistant')
        print(f'\n######## 第 {k} 步 {clock}  用户：{line[:60]}')
        print(f'调用 {len(calls_all)} 次：', calls_all[:6])
        print(f'--- 思考（共 {len(think)} 字符，尾部）\n' + think[-nt:])
        print('--- 回复\n' + (a.get('content') or '')[:nr], flush=True)


if __name__ == '__main__':
    main()
