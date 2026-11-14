import base64
import json
from sampledata import samples


def response(raw, status=200, headers=None, **options):
    return dict(base64=base64.b64encode(raw).decode(), status=status,
                headers=headers or {}, chunk_bytes=3, **options)


def scenario():
    fixtures = samples()
    routes = {}
    routes['GET /openapi'] = [response(fixtures['sample.json'], status=200)]

    routes['GET /items?ids=a,b'] = [dict(status=200, text='{"items":["a",null,"b"]}',
                                         headers={'Content-Type': 'application/json'},
                                         require_headers={'X-Key': 'local-synthetic-key'})]
    routes['POST /items'] = [dict(status=405, text='未批准写操作')]

    # 供应商 A 回环演练：一条结构合法、一条 HTTP 200 但结构不符（缺必填 name）
    routes['GET /v1/items?ids=a,b&tenant=main'] = [
        dict(status=200,
             text=json.dumps({'items': [
                 {'id': 'a', 'name': 'alpha', 'category': {'code': 'c1', 'label': None},
                  'tags': ['x', None]}],
                 'next': None}, ensure_ascii=False),
             headers={'Content-Type': 'application/json'},
             require_headers={'X-Key': 'local-synthetic-key'})]
    routes['GET /v1/items?ids=bad&tenant=main'] = [
        dict(status=200,
             text=json.dumps({'items': [{'id': 'b'}]}, ensure_ascii=False),
             headers={'Content-Type': 'application/json'},
             require_headers={'X-Key': 'local-synthetic-key'})]
    routes['POST /v1/items'] = [dict(status=405, text='未批准写操作')]
    return routes


def main():
    import argparse
    from transport import ReplaySource
    parser = argparse.ArgumentParser(description='来源协议本地演练入口')
    parser.add_argument('--export', action='store_true')
    args = parser.parse_args()
    routes = scenario()
    if args.export:
        print(json.dumps(routes, ensure_ascii=False, sort_keys=True))
        return
    with ReplaySource(routes) as server:
        print(json.dumps({'url': server.url, 'routes': list(routes)}, ensure_ascii=False), flush=True)
        try:
            server.thread.join()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
