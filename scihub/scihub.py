# -*- coding: utf-8 -*-

import re
import argparse
import hashlib
import logging
import os
import asyncio
import aiohttp
from bs4 import BeautifulSoup

# log config
logging.basicConfig()
logger = logging.getLogger('Sci-Hub')
logger.setLevel(logging.DEBUG)

# constants
SCHOLARS_BASE_URL = 'https://scholar.google.com/scholar'
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:27.0) Gecko/20100101 Firefox/27.0'}

class SciHub:
    def __init__(self):
        self.session = aiohttp.ClientSession()
        self.available_base_url_list = []
        self.base_url = ''

    async def get_available_scihub_urls(self):
        urls = []
        async with self.session.get('https://sci-hub.now.sh/') as res:
            content = await res.text()
            s = self.get_soup(content)
            for a in s.find_all('a', href=True):
                if 'sci-hub.' in a['href']:
                    urls.append(a['href'])
        return urls

    async def init_session(self):
        self.available_base_url_list = await self.get_available_scihub_urls()
        self.base_url = self.available_base_url_list[0] + '/'

    async def search(self, query, limit=10, download=False):
        start = 0
        results = {'papers': []}

        while True:
            try:
                async with self.session.get(SCHOLARS_BASE_URL, params={'q': query, 'start': start}) as res:
                    content = await res.text()
                s = self.get_soup(content)
                papers = s.find_all('div', class_="gs_r")

                if not papers:
                    if 'CAPTCHA' in content:
                        results['err'] = 'Failed to complete search with query %s (captcha)' % query
                    return results

                for paper in papers:
                    if not paper.find('table'):
                        source = None
                        pdf = paper.find('div', class_='gs_ggs gs_fl')
                        link = paper.find('h3', class_='gs_rt')

                        if pdf:
                            source = pdf.find('a')['href']
                        elif link.find('a'):
                            source = link.find('a')['href']
                        else:
                            continue

                        results['papers'].append({
                            'name': link.text,
                            'url': source
                        })

                        if len(results['papers']) >= limit:
                            return results

                start += 10

    async def download(self, identifier, destination='', path=None):
        data = await self.fetch(identifier)

        if not 'err' in data:
            self.save(data['pdf'], os.path.join(destination, path if path else data['name']))

        return data

    async def fetch(self, identifier):
        try:
            url = self.get_direct_url(identifier)
            async with self.session.get(url, verify_ssl=False) as res:
                if res.headers['Content-Type'] != 'application/pdf':
                    self.change_base_url()
                    logger.info('Failed to fetch pdf with identifier %s '
                                '(resolved url %s) due to captcha' % (identifier, url))
                    raise CaptchaNeedException('Failed to fetch pdf with identifier %s '
                                               '(resolved url %s) due to captcha' % (identifier, url))
                else:
                    return {
                        'pdf': await res.read(),
                        'url': url,
                        'name': self.generate_name(res)
                    }

        except aiohttp.ClientConnectionError:
            logger.info('Cannot access {}, changing url'.format(self.available_base_url_list[0]))
            self.change_base_url()

        except aiohttp.ClientError as e:
            logger.info('Failed to fetch pdf with identifier %s (resolved url %s) due to request exception.'
                       % (identifier, url))
            return {
                'err': 'Failed to fetch pdf with identifier %s (resolved url %s) due to request exception.'
                       % (identifier, url)
            }

    def get_direct_url(self, identifier):
        id_type = self.classify(identifier)
        return identifier if id_type == 'url-direct' else self.search_direct_url(identifier)

    async def init(self):
        await self.init_session()

    def classify(self, identifier):
        if (identifier.startswith('http') or identifier.startswith('https')):
            if identifier.endswith('pdf'):
                return 'url-direct'
            else:
                return 'url-non-direct'
        elif identifier.isdigit():
            return 'pmid'
        else:
            return 'doi'

    def search_direct_url(self, identifier):
        async with self.session.get(self.base_url + identifier, verify_ssl=False) as res:
            content = await res.text()
            s = self.get_soup(content)
            iframe = s.find('iframe')
            if iframe:
                return iframe.get('src') if not iframe.get('src').startswith('//') else 'http:' + iframe.get('src')

    def save(self, data, path):
        with open(path, 'wb') as f:
            f.write(data)

    def get_soup(self, html):
        return BeautifulSoup(html, 'html.parser')

    def generate_name(self, res):
        name = res.url.split('/')[-1]
        name = re.sub('#view=(.+)', '', name)
        pdf_hash = hashlib.md5(res.content).hexdigest()
        return '%s-%s' % (pdf_hash, name[-20:])

    def change_base_url(self):
        if not self.available_base_url_list:
            raise Exception('Ran out of valid sci-hub urls')
        del self.available_base_url_list[0]
        self.base_url = self.available_base_url_list[0] + '/'
        logger.info("I'm changing to {}".format(self.available_base_url_list[0]))


class CaptchaNeedException(Exception):
    pass

async def main():
    sh = SciHub()
    await sh.init()

    parser = argparse.ArgumentParser(description='SciHub - To remove all barriers in the way of science.')
    parser.add_argument('-d', '--download', metavar='(DOI|PMID|URL)', help='tries to find and download the paper',
                        type=str)
    parser.add_argument('-f', '--file', metavar='path', help='pass file with a list of identifiers and download each',
                        type=str)
    parser.add_argument('-s', '--search', metavar='query', help='search Google Scholars', type=str)
    parser.add_argument('-sd', '--search_download', metavar='query',
                        help='search Google Scholars and download if possible', type=str)
    parser.add_argument('-l', '--limit', metavar='N', help='the number of search results to limit to', default=10,
                        type=int)
    parser.add_argument('-o', '--output', metavar='path', help='directory to store papers', default='', type=str)
    parser.add_argument('-v', '--verbose', help='increase output verbosity', action='store_true')
    parser.add_argument('-p', '--proxy', help='via proxy format like socks5://user:pass@host:port', action='store',
                        type=str)

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    if args.proxy:
        sh.set_proxy(args.proxy)

    if args.download:
        result = await sh.download(args.download, args.output)
        if 'err' in result:
            logger.debug('%s', result['err'])
        else:
            logger.debug('Successfully downloaded file with identifier %s', args.download)
    elif args.search:
        results = await sh.search(args.search, args.limit)
        if 'err' in results:
            logger.debug('%s', results['err'])
        else:
            logger.debug('Successfully completed search with query %s', args.search)
        print(results)
    elif args.search_download:
        results = await sh.search(args.search_download, args.limit)
        if 'err' in results:
            logger.debug('%s', results['err'])
        else:
            logger.debug('Successfully completed search with query %s', args.search_download)
            for paper in results['papers']:
                result = await sh.download(paper['url'], args.output)
                if 'err' in result:
                    logger.debug('%s', result['err'])
                else:
                    logger.debug('Successfully downloaded file with identifier %s', paper['url'])
    elif args.file:
        with open(args.file, 'r') as f:
            identifiers = f.read().splitlines()
            for identifier in identifiers:
                result = await sh.download(identifier, args.output)
                if 'err' in result:
                    logger.debug('%s', result['err'])
                else:
                    logger.debug('Successfully downloaded file with identifier %s', identifier)

if __name__ == '__main__':
    asyncio.run(main())
