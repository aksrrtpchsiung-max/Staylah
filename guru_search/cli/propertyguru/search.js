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
  // B invokes search and detail as separate OpenCLI processes. Reuse the site
  // session so OpenCLI does not close the PropertyGuru tab between commands.
  siteSession: 'persistent',
  navigateBefore: false, // The adapter navigates directly to the requested search URL.
  args: [
    { name: 'query', type: 'string', positional: true, required: true, help: 'Location to search: district, MRT station, or area name (e.g. "clementi", "jurong west", "paya lebar")' },
    { name: 'listing', type: 'string', default: 'rent', choices: ['rent', 'sale'], help: 'Listing type: rent (default) or sale' },
    { name: 'max', type: 'int', help: 'Maximum monthly rent (SGD)' },
    { name: 'min', type: 'int', help: 'Minimum monthly rent (SGD)' },
    { name: 'bedrooms', type: 'int', help: 'Number of bedrooms' },
    { name: 'bedroom-buckets', type: 'string', help: 'Explicit website buckets, e.g. 2,3,4,5 (5 means 5+)' },
    { name: 'min-bedrooms', type: 'int', help: 'Minimum bedrooms; selects all matching website bedroom buckets' },
    { name: 'rental-scope', type: 'string', choices: ['whole_unit', 'room'], help: 'Entire unit or room only' },
    { name: 'room-type', type: 'string', choices: ['common', 'master', 'shared'], help: 'Room type for room-only rentals' },
    { name: 'property-group', type: 'string', choices: ['H', 'N', 'L'], help: 'Verified website property group' },
    { name: 'property-codes', type: 'string', help: 'Verified condo subtypes: CONDO,EXCON' },
    { name: 'type', type: 'string', choices: PROPERTY_TYPES, help: 'Property type: hdb, condo, landed, semi-d, etc.' },
    { name: 'limit', type: 'int', default: 20, help: 'Max results (default 20)' },
    { name: 'page', type: 'int', default: 1, help: 'Search result page' },
    { name: 'offset', type: 'int', default: 0, help: 'Resume within a page after a candidate limit' },
    { name: 'output-mode', type: 'string', default: 'listings', choices: ['listings', 'page', 'full-page'], help: 'full-page returns the native page for request-scoped slicing/cache' },
    { name: 'include-media-source', type: 'bool', default: false, help: 'Diagnostic page output: include original card media for live verification' },
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
    if (kwargs['min-bedrooms']) {
      if (kwargs.bedrooms) throw new ArgumentError('bedrooms and min-bedrooms are mutually exclusive');
      const minimum = normalizePositiveInteger(kwargs['min-bedrooms'], null, 'min-bedrooms');
      // Site verified: 2, 3, 4, and 5+ are independent multi-select options; 5 means 5+, not exactly five rooms.
      for (let n = Math.min(minimum, 5); n <= 5; n++) params.append('bedrooms', String(n));
    }
    if (kwargs['bedroom-buckets']) {
      if (kwargs.bedrooms != null || kwargs['min-bedrooms'] != null) {
        throw new ArgumentError('bedroom-buckets cannot be combined with other bedroom options');
      }
      const buckets = String(kwargs['bedroom-buckets']).split(',');
      if (!buckets.length || buckets.some(value => !/^[0-5]$/.test(value))) {
        throw new ArgumentError('bedroom-buckets must contain integers from 0 to 5');
      }
      for (const bucket of new Set(buckets)) params.append('bedrooms', bucket);
    }
    if (kwargs['rental-scope']) {
      if (listingType !== 'rent') throw new ArgumentError('rental-scope only applies to rent');
      params.set('entireUnitOrRoom', kwargs['rental-scope'] === 'whole_unit' ? 'ent' : 'room');
    }
    if (kwargs['room-type']) {
      if (kwargs['rental-scope'] !== 'room') throw new ArgumentError('room-type requires rental-scope=room');
      params.set('roomType', kwargs['room-type']);
    }
    if (kwargs['property-group']) params.set('propertyTypeGroup', kwargs['property-group']);
    if (kwargs['property-codes']) {
      const codes = String(kwargs['property-codes']).split(',');
      if (kwargs.type || codes.some(code => !['CONDO', 'EXCON'].includes(code))) {
        throw new ArgumentError('property-codes accepts CONDO,EXCON and cannot be combined with type');
      }
      if (kwargs['property-group'] && kwargs['property-group'] !== 'N') {
        throw new ArgumentError('condo property-codes requires property-group=N');
      }
      params.set('propertyTypeGroup', 'N');
      for (const code of new Set(codes)) params.append('propertyTypeCode', code);
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

    // DOM stability can occur before the document has delivered its SSR data.
    // Poll the actual payload with fixed timers, within the provider's 30s budget.
    try {
      await page.goto(searchUrl, { settleMs: 2000 });
    } catch (error) {
      // Chrome can reject reuse of an automation tab. Recover only this exact
      // browser error, once, in a fresh tab; authentication/other errors propagate.
      if (!/^Navigation rejected\.?$/i.test(String(error?.message || error))) throw error;
      await page.closeWindow();
      const target = await page.newTab(searchUrl);
      if (!target) throw error;
      page.setActivePage(target);
    }
    const readyDeadline = Date.now() + 12000;
    let data;
    while (true) {
      data = await page.evaluate((expectedHref) => {
        try {
          if (/verify (?:that )?you are (?:a )?human|complete (?:the )?captcha|checking your browser/i.test(document.body?.innerText || '')
            || /^just a moment[.!]?$/i.test(document.title.trim())) {
            return { error: 'AUTH_REQUIRED: captcha or browser verification is required' };
          }
          // Navigation may still expose the previous document. PropertyGuru also
          // normalizes query keys, e.g. maxprice -> maxPrice and market -> isCommercial.
          const expected = new URL(expectedHref);
          const actual = new URL(window.location.href);
          const actualParams = new URLSearchParams();
          for (const [key, value] of actual.searchParams) actualParams.append(key.replace(/[_-]/g, '').toLowerCase(), value);
          const matchesRequest = actual.origin === expected.origin && actual.pathname === expected.pathname
            && Array.from(expected.searchParams).every(([key, value]) => {
              const normalized = key.replace(/[_-]/g, '').toLowerCase();
              const observed = actualParams.get(normalized);
              if (normalized === 'market' && value === 'residential') {
                return observed === value || actualParams.get('iscommercial') === 'false';
              }
              if (normalized === 'page') return (observed ?? '1') === value;
              if (normalized === 'freetext') return observed?.trim().toLowerCase() === value.trim().toLowerCase();
              return actualParams.getAll(normalized).includes(value);
            });
          if (!matchesRequest) return { pending: true };
          // The bridge may evaluate in an isolated world; read the public SSR script.
          const embedded = document.getElementById('__NEXT_DATA__')?.textContent;
          const d = embedded ? JSON.parse(embedded) : window.__NEXT_DATA__;
          const listingsData = d?.props?.pageProps?.pageData?.data?.listingsData;
          if (!listingsData) return { pending: true };
          if (typeof listingsData !== 'object') return { error: 'PARSE_ERROR: invalid listing data on page' };

          const results = [];
          for (const entry of Object.values(listingsData)) {
            if (!entry?.listingData?.id) continue;
            // “Explore around / Listing with similar price range” cards are ads,
            // not matches for this query: the live page includes over-budget ads.
            // Remove them before applying the candidate limit and page offset.
            if (entry.cardConfig?.['da-id'] === 'promoted-listing-card') continue;
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
          // Only use endpoints explicitly provided by the page; do not infer that the search is complete from "fewer than limit".
          const totalPages = sourceData.pagination?.totalPages ?? sourceData.paginationData?.totalPages ?? null;
          const explicitlyEmpty = /\b(?:no (?:properties|listings|results) found|0 (?:properties|results) found)\b/i.test(document.body?.innerText || '')
            || sourceData.pagination?.totalResults === 0;
          return { results, links, nextDisabled, totalPages, explicitlyEmpty };
        } catch (err) {
          return { error: `PARSE_ERROR: ${err?.message || 'unknown error extracting listings'}` };
        }
      }, searchUrl);
      if (!data?.pending) break;
      const remaining = readyDeadline - Date.now();
      if (remaining <= 0) throw new CommandExecutionError('TEMPORARY_UNAVAILABLE: search page data not ready');
      await new Promise(resolve => setTimeout(resolve, Math.min(300, remaining)));
    }

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
    const fullPage = kwargs['output-mode'] === 'full-page';
    const selected = data.results.slice(fullPage ? 0 : offset, fullPage ? undefined : offset + limit);
    const listings = selected
      .map((raw) => buildSearchListing(raw, {
        baseUrl: BASE_URL,
        requestedListingType: listingType,
        fetchedAt,
      }))
      .filter(Boolean);

    const truncated = !fullPage && offset + limit < data.results.length;
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
    if (['page', 'full-page'].includes(kwargs['output-mode'])) {
      const result = { items: listings, next_cursor: nextCursor, pagination_known: paginationKnown, truncated };
      // Opt-in diagnostics only; normal 3a calls do not duplicate URLs in their payload.
      if (kwargs['include-media-source']) result.media_source = selected.map(raw => ({
        id: String(raw.id), thumbnail: raw.thumbnail ?? null, mediaItems: raw.mediaItems ?? [],
        preview: raw.mediaCarousel?.previewMedia ?? {},
      }));
      return [result];
    }

    return listings;
  },
});
