import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';
import { buildSearchListing } from './contract-listing.js';

const BASE_URL = 'https://www.propertyguru.com.sg';

const PROPERTY_TYPES = [
  'hdb',
  'condo',
  'apartment',
  'landed',
  'semi-d',
  'terraced',
  'detached',
  'bungalow',
  'executive-condo',
  'walk-up',
  'studio',
];

function normalizePositiveInteger(value, defaultValue, label) {
  const raw = value ?? defaultValue;
  const n = Number(raw);
  if (!Number.isSafeInteger(n) || n <= 0) throw new ArgumentError(`${label} must be a positive integer`);
  return n;
}

cli({
  site: 'propertyguru',
  name: 'search',
  access: 'read',
  description: 'Search PropertyGuru and return Falcon contract-compliant listing records. Default: rent, sorted by relevance.',
  domain: 'www.propertyguru.com.sg',
  strategy: Strategy.COOKIE,
  browser: true,
  args: [
    { name: 'query', type: 'string', positional: true, required: true, help: 'Location to search: district, MRT station, or area name (e.g. "clementi", "jurong west", "paya lebar")' },
    { name: 'listing', type: 'string', default: 'rent', choices: ['rent', 'sale'], help: 'Listing type: rent (default) or sale' },
    { name: 'max', type: 'int', help: 'Maximum monthly rent (SGD)' },
    { name: 'min', type: 'int', help: 'Minimum monthly rent (SGD)' },
    { name: 'bedrooms', type: 'int', help: 'Number of bedrooms' },
    { name: 'type', type: 'string', choices: PROPERTY_TYPES, help: 'Property type: hdb, condo, landed, semi-d, etc.' },
    { name: 'limit', type: 'int', default: 20, help: 'Max results (default 20)' },
    { name: 'page', type: 'int', default: 1, help: 'Search result page' },
    { name: 'offset', type: 'int', default: 0, help: 'Resume within a page after a candidate limit' },
    { name: 'output-mode', type: 'string', default: 'listings', choices: ['listings', 'page'], help: 'page includes continuation metadata' },
  ],
  columns: ['listing_key', 'title', 'transaction_type', 'price', 'attributes', 'bedrooms', 'listing_status', 'source_url'],
  func: async (page, kwargs) => {
    const query = String(kwargs.query ?? '').trim();
    if (!query) throw new ArgumentError('search query (location) is required');

    const limit = normalizePositiveInteger(kwargs.limit, 20, 'limit');
    const pageNumber = normalizePositiveInteger(kwargs.page, 1, 'page');
    const offset = Number(kwargs.offset ?? 0);
    if (!Number.isSafeInteger(offset) || offset < 0) throw new ArgumentError('offset must be a non-negative integer');
    const listingType = kwargs.listing || 'rent';
    if (!['rent', 'sale'].includes(listingType)) {
      throw new ArgumentError(`listing must be "rent" or "sale", got "${listingType}"`);
    }

    // Build search URL
    const params = new URLSearchParams();
    params.set('freetext', query);
    params.set('market', 'residential');
    params.set('page', String(pageNumber));

    if (kwargs.max) {
      const max = normalizePositiveInteger(kwargs.max, null, 'max');
      params.set('maxprice', String(max));
    }
    if (kwargs.min) {
      const min = normalizePositiveInteger(kwargs.min, null, 'min');
      params.set('minprice', String(min));
    }
    if (kwargs.bedrooms) {
      params.set('bedrooms', String(normalizePositiveInteger(kwargs.bedrooms, null, 'bedrooms')));
    }
    if (kwargs.type) {
      const pt = String(kwargs.type).toLowerCase().trim();
      if (!PROPERTY_TYPES.includes(pt)) {
        throw new ArgumentError(`Unknown property type "${pt}". Valid: ${PROPERTY_TYPES.join(', ')}`);
      }
      params.set('property_type_code', pt);
    }

    const path = listingType === 'sale' ? '/property-for-sale' : '/property-for-rent';
    const searchUrl = `${BASE_URL}${path}?${params.toString()}`;

    // Navigate and wait for SSR data
    await page.goto(searchUrl, { settleMs: 2000 });
    await page.wait(2);

    const data = await page.evaluate(() => {
      try {
        if (/verify (?:that )?you are human|complete the captcha|checking your browser/i.test(document.body?.innerText || '')) {
          return { error: 'AUTH_REQUIRED: captcha or browser verification is required' };
        }
        // The bridge may evaluate in an isolated world; read the public SSR script.
        const embedded = document.getElementById('__NEXT_DATA__')?.textContent;
        const d = embedded ? JSON.parse(embedded) : window.__NEXT_DATA__;
        if (!d) return { error: 'page did not load properly (no __NEXT_DATA__)' };
        const listingsData = d?.props?.pageProps?.pageData?.data?.listingsData;
        if (!listingsData) return { error: 'could not find listing data on page' };

        const results = [];
        for (const entry of Object.values(listingsData)) {
          if (!entry?.listingData?.id) continue;
          const ld = entry.listingData;
          // Preserve the complete search-card payload for contract normalization.
          // Unknown fields remain null/unknown rather than being guessed.
          results.push(ld);
        }
        const sourceData = d.props.pageProps.pageData.data;
        const links = Array.from(document.querySelectorAll(
          'a[rel="next"], [aria-label*="pagination" i] a, [class*="pagination"] a',
        )).map(a => a.href).filter(Boolean);
        const nextDisabled = Boolean(document.querySelector(
          '[aria-label*="next" i][disabled], [aria-label*="next" i][aria-disabled="true"]',
        ));
        // 只使用页面明确提供的终点，不用“少于 limit”推测已经查完。
        const totalPages = sourceData.pagination?.totalPages ?? sourceData.paginationData?.totalPages ?? null;
        const explicitlyEmpty = /\b(?:no (?:properties|listings|results) found|0 (?:properties|results) found)\b/i.test(document.body?.innerText || '')
          || sourceData.pagination?.totalResults === 0;
        return { results, links, nextDisabled, totalPages, explicitlyEmpty };
      } catch (err) {
        return { error: err?.message || 'unknown error extracting listings' };
      }
    });

    if (data?.error) {
      if (data.error.includes('not log') || data.error.includes('captcha') || data.error.includes('login')) {
        throw new AuthRequiredError('propertyguru.com.sg', data.error);
      }
      throw new CommandExecutionError(data.error);
    }

    const fetchedAt = new Date().toISOString();
    if (data.results.length === 0 && !data.explicitlyEmpty) {
      throw new CommandExecutionError('PARSE_ERROR: empty listing payload without an explicit no-results state');
    }
    if (offset > data.results.length || (offset > 0 && offset === data.results.length)) {
      throw new CommandExecutionError('PARSE_ERROR: page changed; continuation offset is no longer valid');
    }
    const listings = data.results
      .slice(offset, offset + limit)
      .map((raw) => buildSearchListing(raw, {
        baseUrl: BASE_URL,
        requestedListingType: listingType,
        fetchedAt,
      }))
      .filter(Boolean);

    const truncated = offset + limit < data.results.length;
    const nextPage = data.links.map(link => {
      try {
        const url = new URL(link, BASE_URL);
        if (url.origin !== BASE_URL || url.pathname !== path) return null;
        const number = Number(url.searchParams.get('page'));
        return Number.isSafeInteger(number) && number > pageNumber ? number : null;
      } catch { return null; }
    }).filter(number => number !== null).sort((a, b) => a - b)[0] ?? null;
    const totalPages = Number(data.totalPages);
    const hasPageCount = data.totalPages !== null && Number.isSafeInteger(totalPages) && totalPages >= pageNumber;
    const following = nextPage ?? (hasPageCount && pageNumber < totalPages ? pageNumber + 1 : null);
    const paginationKnown = truncated || following !== null || data.nextDisabled || hasPageCount || data.explicitlyEmpty;
    const nextCursor = truncated ? `pg:v1:${pageNumber}:${offset + limit}`
      : following !== null ? `pg:v1:${following}:0` : null;
    if ((kwargs['output-mode'] ?? 'listings') === 'page') {
      return [{ items: listings, next_cursor: nextCursor, pagination_known: paginationKnown, truncated }];
    }

    return listings;
  },
});
