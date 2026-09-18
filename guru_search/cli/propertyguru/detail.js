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
  args: [
    { name: 'id', type: 'string', positional: true, required: true, help: 'Listing ID (numeric) or full URL from search results' },
    { name: 'output-mode', type: 'string', default: 'summary', choices: ['summary', 'structured'], help: 'structured preserves facts and raw details' },
  ],
  columns: ['id', 'title', 'price', 'description', 'detailItems', 'amenities', 'facilities', 'nearbyMrt', 'url'],
  func: async (page, kwargs) => {
    const url = resolveListingUrl(kwargs.id);
    await page.goto(url, { settleMs: 2000 });
    await page.wait(2);

    // Some detail responses expose their embedded payload later than the fixed settle time.
    // Wait for the actual payload; never turn an unloaded page into an empty successful detail.
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const ready = await page.evaluate(() => {
        const embedded = document.getElementById('__NEXT_DATA__')?.textContent;
        try {
          const data = embedded ? JSON.parse(embedded) : window.__NEXT_DATA__;
          return Boolean(data?.props?.pageProps?.pageData?.data);
        } catch {
          return false;
        }
      });
      if (ready || attempt === 3) break;
      await page.wait(1);
    }

    const data = await page.evaluate(() => {
      try {
        if (/verify (?:that )?you are human|complete the captcha|checking your browser/i.test(document.body?.innerText || '')) {
          return { error: 'AUTH_REQUIRED: captcha or browser verification is required' };
        }
        const embedded = document.getElementById('__NEXT_DATA__')?.textContent;
        const nextData = embedded ? JSON.parse(embedded) : window.__NEXT_DATA__;
        const d = nextData?.props?.pageProps?.pageData?.data;
        if (!d) return { error: 'TEMPORARY_UNAVAILABLE: detail page data not ready' };

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
          value: i.value || '',
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
        return { error: err?.message || 'unknown error' };
      }
    });

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
