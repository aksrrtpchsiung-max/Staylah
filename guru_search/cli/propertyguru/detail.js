import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';
import { buildListingDetail } from './contract-listing.js';

const BASE_URL = 'https://www.propertyguru.com.sg';

function resolveListingUrl(input) {
  const raw = String(input ?? '').trim();
  if (!raw) throw new ArgumentError('listing id or url is required');
  // Full URL: extract and use directly
  if (raw.startsWith('https://www.propertyguru.com.sg/listing/')) return raw;
  // Numeric ID: construct URL (PropertyGuru redirects slug-less URLs)
  if (/^\d+$/.test(raw)) return `${BASE_URL}/listing/${raw}`;
  throw new ArgumentError(`Invalid listing id or url: "${raw}". Pass a numeric ID or full propertyguru.sg listing URL.`);
}

cli({
  site: 'propertyguru',
  name: 'detail',
  access: 'read',
  description: 'Get full details for a PropertyGuru listing — description, amenities, facilities, nearby POIs.',
  domain: 'www.propertyguru.com.sg',
  strategy: Strategy.COOKIE,
  browser: true,
  // B invokes search and detail as separate OpenCLI processes. Reuse the site
  // session so OpenCLI does not close the PropertyGuru tab between commands.
  siteSession: 'persistent',
  navigateBefore: false, // Avoid an unrelated homepage navigation before the listing URL.
  args: [
    { name: 'id', type: 'string', positional: true, required: true, help: 'Listing ID (numeric) or full URL from search results' },
    { name: 'output-mode', type: 'string', default: 'summary', choices: ['summary', 'structured'], help: 'structured preserves facts and raw details' },
  ],
  columns: ['id', 'title', 'price', 'description', 'detailItems', 'amenities', 'facilities', 'nearbyMrt', 'url'],
  func: async (page, kwargs) => {
    const url = resolveListingUrl(kwargs.id);
    try {
      await page.goto(url, { settleMs: 2000 });
    } catch (error) {
      // Release only this adapter's tab lease; never reset the user's browser.
      if (!/^Navigation rejected\.?$/i.test(String(error?.message || error))) throw error;
      await page.closeWindow();
      const target = await page.newTab(url);
      if (!target) throw error;
      page.setActivePage(target);
    }
    // page.wait(number) returns early when the DOM is quiet. A fixed timer
    // between payload checks also handles a quiet document that is still loading.
    const readyDeadline = Date.now() + 12000;
    let data;
    while (true) {
      data = await page.evaluate((expectedHref) => {
        try {
          if (/verify (?:that )?you are (?:a )?human|complete (?:the )?captcha|checking your browser/i.test(document.body?.innerText || '')
            || /^just a moment[.!]?$/i.test(document.title.trim())) {
            return { error: 'AUTH_REQUIRED: captcha or browser verification is required' };
          }
          const expected = new URL(expectedHref);
          const actual = new URL(window.location.href);
          const listingId = pathname => pathname.match(/(?:\/|\-)(\d+)\/?$/)?.[1];
          if (actual.origin !== expected.origin || !actual.pathname.startsWith('/listing/')
            || listingId(actual.pathname) !== listingId(expected.pathname)) return { pending: true };
          const embedded = document.getElementById('__NEXT_DATA__')?.textContent;
          const nextData = embedded ? JSON.parse(embedded) : window.__NEXT_DATA__;
          const d = nextData?.props?.pageProps?.pageData?.data;
          if (!d) return { pending: true };
          if (typeof d !== 'object') return { error: 'PARSE_ERROR: invalid detail data on page' };
          if (!d.detailsData && !d.propertyOverviewData) return { pending: true };

          const desc = d.descriptionBlockData;
          const details = d.detailsData;
          const amenities = d.amenitiesData;
          const facilities = d.facilitiesData;
          const overview = d.propertyOverviewData;
          const listingLocation = d.listingDetail?.location;
          const listingAddress = listingLocation?.address;
          const street = listingLocation?.streetName || listingLocation?.street;
          const block = listingAddress?.block || listingAddress?.streetNumber;
          const streetAddress = street && [block, street].filter(Boolean).join(' ');
          // This is the listing's own address, not the nearby-POI template in locationInfo.
          const locationInfo = {
            address: streetAddress || listingAddress?.formatted || overview?.propertyInfo?.fullAddress || null,
            postalCode: listingAddress?.postalCode || d.listingData?.postcode || null,
            sourceFormattedAddress: listingAddress?.formatted || null,
          };

          // Extract detail items as key-value pairs
          const detailItems = (details?.metatable?.items || []).map(i => ({
            label: i.label || i.title || i.icon || '',
            icon: i.icon || '',
            value: i.value ?? '',
          }));

          // Extract amenities
          const amenityList = Array.isArray(amenities?.data)
            ? amenities.data.map(a => (typeof a === 'string' ? a : a?.label || a?.name || '')).filter(Boolean)
            : [];

          // Extract facilities
          const facilityList = Array.isArray(facilities?.data)
            ? facilities.data.map(f => (typeof f === 'string' ? f : f?.label || f?.name || '')).filter(Boolean)
            : [];

          // Nearby MRT (from location data, fetched client-side, try to get from DOM)
          const mrtEls = document.querySelectorAll('[class*="poi"] [class*="station"], .poi-station-name');
          const nearbyMrt = Array.from(mrtEls).slice(0, 5).map(el => el.textContent?.trim()).filter(Boolean);

          return {
            description: desc?.description || '',
            subtitle: desc?.subtitle || '',
            detailItems,
            amenityList,
            facilityList,
            nearbyMrt,
            propertyInfo: overview?.propertyInfo || null,
            locationInfo,
            canonicalUrl: document.querySelector('link[rel="canonical"]')?.href || window.location.href,
          };
        } catch (err) {
          return { error: `PARSE_ERROR: ${err?.message || 'unknown error extracting detail'}` };
        }
      }, url);
      if (!data?.pending) break;
      const remaining = readyDeadline - Date.now();
      if (remaining <= 0) throw new CommandExecutionError('TEMPORARY_UNAVAILABLE: detail page data not ready');
      await new Promise(resolve => setTimeout(resolve, Math.min(300, remaining)));
    }

    if (data?.error?.startsWith('AUTH_REQUIRED:')) throw new AuthRequiredError('propertyguru.com.sg', data.error);
    if (data?.error) throw new CommandExecutionError(data.error);

    if ((kwargs['output-mode'] ?? 'summary') === 'structured') {
      const canonical = new URL(data.canonicalUrl || url, BASE_URL);
      const sourceId = canonical.pathname.match(/(?:\/|\-)(\d+)\/?$/)?.[1];
      if (canonical.origin !== BASE_URL || !canonical.pathname.startsWith('/listing/') || !sourceId) {
        throw new CommandExecutionError('PARSE_ERROR: detail page does not identify a PropertyGuru listing');
      }
      const requestedId = new URL(url).pathname.match(/(?:\/|\-)(\d+)\/?$/)?.[1];
      if (requestedId && requestedId !== sourceId) {
        throw new CommandExecutionError('PARSE_ERROR: detail redirected to a different listing');
      }
      return [buildListingDetail(data, { sourceId, sourceUrl: canonical.href })];
    }

    // Build a summary row
    const detailSummary = data.detailItems.map(i => `${i.value}`).join(' | ');

    return [{
      id: kwargs.id,
      title: data.subtitle || '',
      price: '',
      description: (data.description || '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, ''),
      detailItems: detailSummary,
      amenities: data.amenityList.join(', '),
      facilities: data.facilityList.join(', '),
      nearbyMrt: data.nearbyMrt.join(', '),
      url,
    }];
  },
});
