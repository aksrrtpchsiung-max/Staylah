import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, CommandExecutionError, EmptyResultError } from '@jackwener/opencli/errors';

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
  ],
  columns: ['id', 'title', 'price', 'description', 'detailItems', 'amenities', 'facilities', 'nearbyMrt', 'url'],
  func: async (page, kwargs) => {
    const url = resolveListingUrl(kwargs.id);
    await page.goto(url, { settleMs: 2000 });
    await page.wait(2);

    const data = await page.evaluate(() => {
      try {
        const d = window.__NEXT_DATA__?.props?.pageProps?.pageData?.data;
        if (!d) return { error: 'page did not load properly' };

        const desc = d.descriptionBlockData;
        const details = d.detailsData;
        const amenities = d.amenitiesData;
        const facilities = d.facilitiesData;
        const overview = d.propertyOverviewData;

        // Extract detail items as key-value pairs
        const detailItems = (details?.metatable?.items || []).map(i => ({
          label: i.icon || '',
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
          locationInfo: overview?.locationInfo || null,
        };
      } catch (err) {
        return { error: err?.message || 'unknown error' };
      }
    });

    if (data?.error) throw new CommandExecutionError(data.error);

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
