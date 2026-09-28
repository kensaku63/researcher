#!/usr/bin/env python3
"""Search arXiv, YouTube, or recent X posts once; print JSON to stdout."""
import argparse
from datetime import datetime, timezone
import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


def secret(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ValueError(f'{name} is not set. Configure the session environment and retry.')
    return value


def get(url, params, headers=None):
    request = urllib.request.Request(
        url + '?' + urllib.parse.urlencode(params),
        headers=headers or {},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def search(source, query, limit):
    if source == 'paper':
        raw = get('https://export.arxiv.org/api/query', {
            'search_query': query, 'start': 0, 'max_results': limit,
        })
        root = ET.fromstring(raw)
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        if root.tag != '{http://www.w3.org/2005/Atom}feed':
            raise ValueError('Unexpected arXiv response (not an Atom feed).')
        items = []
        for entry in root.findall('a:entry', ns):
            def field(name):
                return ' '.join(entry.findtext('a:' + name, '', ns).split())
            if '/api/errors' in field('id'):
                raise ValueError('arXiv rejected the query. Check arXiv query syntax.')
            items.append({'url': field('id'), 'title': field('title'),
                          'authors': [a.text for a in entry.findall('a:author/a:name', ns)],
                          'published_at': field('published'), 'abstract': field('summary')})
        return items
    if source == 'youtube':
        data = json.loads(get('https://www.googleapis.com/youtube/v3/search', {
            'key': secret('YOUTUBE_API_KEY'), 'part': 'snippet', 'type': 'video',
            'q': query, 'maxResults': limit, 'order': 'relevance',
        }))
        if 'error' in data or 'items' not in data:
            raise ValueError('YouTube returned an error or unexpected response.')
        return [{'url': 'https://www.youtube.com/watch?v=' + item['id']['videoId'],
                 'title': html.unescape(item['snippet']['title']),
                 'channel': item['snippet']['channelTitle'],
                 'published_at': item['snippet']['publishedAt'],
                 'description': html.unescape(item['snippet']['description'])}
                for item in data['items']]
    data = json.loads(get('https://api.x.com/2/tweets/search/recent', {
        'query': query, 'max_results': max(10, limit),
        'tweet.fields': 'created_at,author_id',
        'expansions': 'author_id', 'user.fields': 'username',
    }, {'Authorization': 'Bearer ' + secret('X_BEARER_TOKEN')}))
    if data.get('errors') or ('data' not in data and data.get('meta', {}).get('result_count') != 0):
        raise ValueError('X returned an error or incomplete response. Check API access and query.')
    users = {u['id']: u['username'] for u in data.get('includes', {}).get('users', [])}
    return [{'url': 'https://x.com/i/web/status/' + item['id'],
             'text': item['text'], 'author_id': item.get('author_id'),
             'username': users.get(item.get('author_id')),
             'published_at': item.get('created_at')}
            for item in data.get('data', [])][:limit]


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Python standard library only. paper: no key; youtube: YOUTUBE_API_KEY; '
        'x: X_BEARER_TOKEN. One request, no pagination/retries; API quota may be consumed. '
        'X fetches at least 10 posts even if --limit is smaller. Errors go to stderr.'))
    parser.add_argument('source', choices=['paper', 'youtube', 'x'])
    parser.add_argument('query', help='Search text or native provider query syntax')
    parser.add_argument('--limit', type=int, default=5, help='Returned items: 1-50 (default: 5)')
    args = parser.parse_args()
    if not args.query.strip():
        parser.error('query must not be empty')
    if not 1 <= args.limit <= 50:
        parser.error('--limit must be between 1 and 50')
    try:
        items = search(args.source, args.query, args.limit)
    except urllib.error.HTTPError as exc:
        hints = {400: 'Check query syntax and API configuration.',
                 401: 'Check credentials.', 402: 'Check API credits.',
                 403: 'Check API enablement, key restrictions, access and quota.',
                 429: 'Rate limit or quota reached; do not retry immediately.'}
        print(f'HTTP {exc.code}: {hints.get(exc.code, "Provider request failed.")}', file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError):
        print('Network request failed or timed out. Check connectivity and retry later.', file=sys.stderr)
        return 1
    except (ValueError, ET.ParseError, KeyError, TypeError) as exc:
        # Only our controlled ValueError messages are safe to print; response data can be sensitive.
        message = str(exc) if type(exc) is ValueError else 'Unexpected provider response.'
        print(message, file=sys.stderr)
        return 1
    print(json.dumps({'source': args.source, 'query': args.query,
                      'fetched_at': datetime.now(timezone.utc).isoformat(),
                      'count': len(items), 'items': items}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
