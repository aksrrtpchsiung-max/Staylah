const SOURCE = 'propertyguru';
const DEFAULT_CURRENCY = 'SGD';

function text(value) {
  if (value === null || value === undefined) return null;
  const normalized = String(value).trim();
  return normalized || null;
}

function valueAt(object, path) {
  return path.split('.').reduce((current, key) => current?.[key], object);
}

function firstValue(object, paths) {
  for (const path of paths) {
    const value = valueAt(object, path);
    if (value !== null && value !== undefined && value !== '') return value;
  }
  return null;
}

function integer(value) {
  if (typeof value === 'number' && Number.isInteger(value) && value >= 0) return value;
  const raw = text(value);
  if (!raw) return null;
  const normalized = raw.replace(/,/g, '');
  if (!/^\d+(?:\.0+)?(?:\s*(?:sq\.?\s*ft|sqft))?$/i.test(normalized)) return null;
  const parsed = Number.parseFloat(normalized);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function priceAmount(value) {
  const direct = integer(value);
  if (direct !== null) return direct;
  const raw = text(value);
  if (!raw) return null;
  // 不把范围、每平方英尺单价或小数截断成一个确定的挂牌金额。
  const match = raw.match(/^(?:S\$|SGD\s*|\$)?\s*(\d[\d,]*)(?:\.0+)?(?:\s*(?:\/\s*(?:mo(?:nth)?|week|wk)|per\s+(?:month|week)|pcm|pw))?$/i);
  return match ? integer(match[1]) : null;
}

function yesNo(value) {
  if (typeof value === 'boolean') return value;
  const raw = text(value)?.toLowerCase();
  if (['yes', 'true', 'allowed', 'included'].includes(raw)) return true;
  if (['no', 'false', 'not allowed', 'not included'].includes(raw)) return false;
  return null;
}

function normalizedDate(value) {
  const raw = text(value);
  if (!raw) return null;
  const iso = raw.match(/^(\d{4}-\d{2}-\d{2})(?:[tT ][\d:.+-]+[zZ]?)?$/);
  let result = iso?.[1] || null;
  if (!result) {
    const listed = raw.match(/^(?:Listed on\s+)?(\d{1,2})\s+([a-z]+)\s+(\d{4})$/i);
    const months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
    const fullMonths = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
    if (!listed) return null;
    const monthName = listed[2].toLowerCase();
    const month = months.indexOf(monthName) >= 0 ? months.indexOf(monthName) : fullMonths.indexOf(monthName);
    if (month < 0) return null;
    result = `${listed[3]}-${String(month + 1).padStart(2, '0')}-${listed[1].padStart(2, '0')}`;
  }
  // 不让日期解析器把无效的月底日期自动滚到下个月。
  const parsed = new Date(`${result}T00:00:00.000Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === result ? result : null;
}

function normalizePropertyType(raw) {
  const value = text(raw)?.toLowerCase();
  if (!value) return 'unknown';
  if (/\bhdb\b|housing.?development/.test(value)) return 'hdb';
  if (/condo|minium|executive.?condo/.test(value)) return 'condo';
  if (/landed|bungalow|terraced|semi.?detached|detached/.test(value)) return 'landed';
  if (/apartment|walk.?up/.test(value)) return 'apartment';
  return value === 'other' ? 'other' : 'unknown';
}

function normalizeScope(raw) {
  const value = text(raw)?.toLowerCase();
  if (!value) return null;
  if (/bed.?space|shared.?room/.test(value)) return 'bedspace';
  if (/whole.?unit|entire.?unit|full.?unit/.test(value)) return 'whole_unit';
  if (/master.?room|common.?room|room\s+(?:rental|for rent)|room.?only|^room$/.test(value)) return 'room';
  return null;
}

function normalizeRoomType(raw) {
  const value = text(raw)?.toLowerCase();
  if (/master/.test(value || '')) return 'master';
  if (/common/.test(value || '')) return 'common';
  if (/shared|bed.?space/.test(value || '')) return 'shared';
  return 'unknown';
}

function normalizeFurnishing(raw) {
  const value = text(raw)?.toLowerCase().replace(/\s+/g, ' ');
  if (/^fully(?: furnished)?$/.test(value || '')) return 'fully';
  if (/^partial(?:ly)?(?: furnished)?$/.test(value || '')) return 'partially';
  if (value === 'unfurnished') return 'unfurnished';
  return 'unknown';
}

const DETAIL_BOOLEAN_STATEMENTS = {
  owner_stays: { 'staying with owner': true, 'no owner stays': false },
  utilities_included: { 'utilities included': true, 'utilities not included': false },
  wifi_included: { 'wi-fi included': true, 'wi-fi not included': false, 'wifi included': true, 'wifi not included': false },
  visitors_allowed: { 'visitors allowed': true, 'visitors not allowed': false },
  pets_allowed: { 'pets allowed': true, 'pets not allowed': false },
};

function detailBoolean(field, value) {
  const statement = text(value)?.toLowerCase().replace(/\s+/g, ' ');
  const choices = DETAIL_BOOLEAN_STATEMENTS[field];
  return Object.hasOwn(choices, statement) ? choices[statement] : null;
}

function normalizeCooking(value) {
  const statement = text(value)?.toLowerCase().replace(/\s+/g, ' ');
  if (/^(?:none|no cooking|cooking not allowed)$/.test(statement || '')) return 'none';
  if (/^light(?: cooking(?: allowed)?)?$/.test(statement || '')) return 'light';
  if (/^full(?: cooking(?: allowed)?)?$/.test(statement || '')) return 'full';
  return 'unknown';
}

function tenureYears(value) {
  const match = text(value)?.match(/^(\d+)[-\s]+years?\s+lease(?:hold)?$/i);
  return match ? integer(match[1]) : null;
}

function normalizeTenure(raw) {
  const value = text(raw)?.toLowerCase();
  if (/freehold/.test(value || '')) return 'freehold';
  if (/leasehold/.test(value || '')) return 'leasehold';
  return 'unknown';
}

function normalizeTransactionType(raw, fallback) {
  const value = text(raw)?.toLowerCase();
  if (/sale|buy/.test(value || '')) return 'sale';
  if (/rent/.test(value || '')) return 'rent';
  return fallback === 'sale' ? 'sale' : 'rent';
}

function normalizePricePeriod(raw) {
  const value = text(raw)?.toLowerCase() || '';
  if (/\b(?:week|weekly|wk|pw)\b/.test(value)) return 'week';
  if (/\b(?:month|monthly|mo|pcm)\b/.test(value)) return 'month';
  if (value === 'total') return 'total';
  return null;
}

function sourceExcerpt(label, rawValue) {
  const value = typeof rawValue === 'string' ? rawValue.trim() : JSON.stringify(rawValue);
  return `PropertyGuru search result: ${label}=${value}`;
}

/** Search-card media only: no gallery requests, guessed URLs or image downloads. */
export function extractSearchCardPhotos(raw, baseUrl) {
  const preview = raw?.mediaCarousel?.previewMedia;
  const images = [];
  const byIdentity = new Map();
  const seenUrls = new Set();
  const issues = [];
  const sourceId = text(raw?.id);
  const add = (value, kind, caption = null) => {
    if (typeof value !== 'string' || !value.trim()) return;
    let url;
    try {
      url = new URL(value.trim(), baseUrl);
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error();
    } catch {
      issues.push('invalid_image_url');
      return;
    }
    // CDN photo identity groups V800/V550 without modifying either actual URL.
    const pgCdn = /(^|\.)pgimgs\.com$/i.test(url.hostname);
    const listing = pgCdn && url.pathname.match(/^\/listing\/(\d+)\//);
    if (listing && listing[1] !== sourceId) {
      issues.push('image_listing_id_mismatch');
      return;
    }
    const asset = pgCdn && url.pathname.match(/\/([A-Z]+\.\d+)(?:\.|\/|$)/);
    if (kind === 'thumbnail' && asset && asset[1].startsWith('UMOV.')) {
      kind = 'video_thumbnail'; // Retain the link, never count it as a property photo.
    }
    const identity = asset ? `pg:${asset[1]}` : url.href;
    if (seenUrls.has(url.href)) return;
    seenUrls.add(url.href);
    let item = byIdentity.get(identity);
    if (!item) {
      item = { image_id: identity, kind, urls: [], caption: text(caption) };
      images.push(item);
      byIdentity.set(identity, item);
    }
    item.urls.push(url.href);
  };
  for (const [group, kind] of [['images', 'photo'], ['floorPlans', 'floor_plan'], ['sitePlans', 'site_plan']]) {
    const entries = preview?.[group]?.items;
    if (entries != null && !Array.isArray(entries)) issues.push(`invalid_${group}_items`);
    for (const entry of Array.isArray(entries) ? entries : []) {
      if (typeof entry?.src !== 'string' || !entry.src.trim()) {
        issues.push('missing_image_src');
        continue;
      }
      add(entry.src, kind, entry.caption);
    }
  }
  // Keep the card cover even when it is an extra size of the first gallery photo.
  add(raw?.thumbnail, 'thumbnail');
  const counts = (Array.isArray(raw?.mediaItems) ? raw.mediaItems : [])
    .filter(item => item?.mediaType === 'images').map(item => integer(item.text));
  const reportedCount = counts.length && counts.every(n => n !== null && n === counts[0]) ? counts[0] : null;
  if (counts.length && reportedCount === null) issues.push('invalid_reported_count');
  // A standalone thumbnail is retained but cannot establish another unique photo.
  const extractedCount = images.filter(item => item.kind === 'photo').length;
  const hasPhotoArray = Array.isArray(preview?.images?.items);
  let status = 'unknown';
  if (!images.length && reportedCount !== 0) status = 'unavailable';
  else if (reportedCount !== null && extractedCount < reportedCount) status = 'partial';
  else if (reportedCount !== null && extractedCount > reportedCount) issues.push('photo_count_mismatch');
  else if (reportedCount !== null && hasPhotoArray && !issues.length) status = 'complete';
  if (!hasPhotoArray) issues.push('photo_array_unavailable');
  return {
    version: 1, images, reported_count: reportedCount, extracted_count: extractedCount,
    status, issues: [...new Set(issues)],
  };
}

/**
 * Convert one PropertyGuru search-card record into Falcon's complete Listing
 * contract. Every contract key is returned. Unknown facts stay null/unknown;
 * callers can later merge detail-page evidence for shortlisted listings.
 */
export function buildSearchListing(raw, { baseUrl, requestedListingType, fetchedAt = new Date().toISOString() }) {
  const sourceId = text(raw?.id);
  if (!sourceId) return null;

  const sourceUrl = new URL(text(raw.url) || `/listing/${sourceId}`, baseUrl).href;
  const listingKey = `${SOURCE}:${sourceId}`;
  const evidence = [];
  const fieldIssues = [];
  const addEvidence = (field, value, sourceField, original = value) => {
    if (value === null || value === undefined || value === '') return [];
    const evidenceId = `${listingKey}:${field}`;
    evidence.push({
      evidence_id: evidenceId,
      field,
      value,
      source_url: sourceUrl,
      observed_at: fetchedAt,
      excerpt: sourceExcerpt(sourceField, original),
    });
    return [evidenceId];
  };

  const title = text(raw.localizedTitle) || '';
  const transactionType = normalizeTransactionType(raw.typeCode, requestedListingType);
  const priceRaw = firstValue(raw, ['price.value', 'priceValue', 'price.pretty', 'priceDisplay']);
  const amount = priceAmount(priceRaw);
  if (priceRaw !== null && amount === null) fieldIssues.push('price.amount:unparseable');

  const featureText = Array.isArray(raw.listingFeatures)
    ? raw.listingFeatures.map((feature) => text(feature?.text)).filter(Boolean).join(' | ')
    : '';
  const classificationText = [
    featureText,
    raw.propertyType,
    raw.localizedTitle,
    raw.fullAddress,
    firstValue(raw, ['listingScope', 'rentalScope', 'unitType']),
  ].filter(Boolean).join(' | ');

  const sourceBedrooms = integer(raw.bedrooms);
  const bathrooms = integer(raw.bathrooms);
  const areaRaw = firstValue(raw, ['floorArea.value', 'floorArea', 'area.value']);
  const areaSqft = integer(areaRaw);
  const propertyType = normalizePropertyType(raw.propertyType || featureText);
  const listingScope = normalizeScope(firstValue(raw, ['listingScope', 'rentalScope', 'unitType']) || classificationText);
  // PropertyGuru uses bedrooms=0 as a card placeholder for room/bedspace
  // listings. It is not a claim that the rented room has zero bedrooms.
  const bedrooms = sourceBedrooms === 0 && ['room', 'bedspace'].includes(listingScope)
    ? null : sourceBedrooms;
  const furnishingRaw = firstValue(raw, ['furnishing', 'furnishingType', 'listingFeatures.furnishing']);
  const tenureRaw = firstValue(raw, ['tenure', 'tenureType', 'propertyTenure']);
  const leaseYears = integer(firstValue(raw, ['leaseYears', 'tenureYears']));
  const listedDateRaw = firstValue(raw, ['postedOn.date', 'postedOn.iso', 'postedOn.value', 'listedDate']);
  const listedDate = normalizedDate(listedDateRaw);
  if (listedDateRaw !== null && listedDate === null) fieldIssues.push('listed_date:unparseable');
  const sourceUpdatedRaw = firstValue(raw, ['updatedAt', 'sourceUpdatedAt', 'lastUpdatedAt']);
  const updatedText = text(sourceUpdatedRaw);
  const sourceUpdatedAt = updatedText && /(?:Z|[+-]\d{2}:\d{2})$/i.test(updatedText)
    && Number.isFinite(Date.parse(updatedText)) ? new Date(updatedText).toISOString() : null;
  if (sourceUpdatedRaw !== null && sourceUpdatedAt === null) fieldIssues.push('source_updated_at:unparseable');

  const priceEvidence = amount === null ? [] : addEvidence('price.amount', amount, 'price', priceRaw);
  const sourceCurrency = text(firstValue(raw, ['price.currency', 'currency']));
  const currency = sourceCurrency || DEFAULT_CURRENCY;
  const currencyEvidence = addEvidence('price.currency', currency, sourceCurrency ? 'price currency' : 'PropertyGuru Singapore marketplace');
  const periodRaw = firstValue(raw, ['price.period', 'price.frequency', 'price.pretty', 'priceDisplay']);
  const period = normalizePricePeriod(periodRaw) || (transactionType === 'sale' ? 'total' : null);
  if (period === null) fieldIssues.push('price.period:unknown');
  addEvidence('transaction_type', transactionType, raw.typeCode ? 'typeCode' : 'search page transaction type', raw.typeCode || requestedListingType);
  addEvidence('title', title, 'localizedTitle');
  if (propertyType !== 'unknown') addEvidence('attributes.property_type', propertyType, 'propertyType/listingFeatures', raw.propertyType || featureText);
  if (listingScope !== null) addEvidence('attributes.listing_scope', listingScope, 'listing scope/title', classificationText);
  if (bedrooms !== null) addEvidence('bedrooms', bedrooms, 'bedrooms');
  if (bathrooms !== null) addEvidence('attributes.bathrooms', bathrooms, 'bathrooms');
  if (areaSqft !== null) addEvidence('attributes.area_sqft', areaSqft, 'floorArea', areaRaw);
  const furnishing = normalizeFurnishing(furnishingRaw);
  if (furnishing !== 'unknown') addEvidence('attributes.furnishing', furnishing, 'furnishing');
  const tenureType = normalizeTenure(tenureRaw);
  if (tenureType !== 'unknown') addEvidence('attributes.tenure_type', tenureType, 'tenure');
  if (leaseYears !== null) addEvidence('attributes.lease_years', leaseYears, 'leaseYears');
  if (listedDate !== null) addEvidence('listed_date', listedDate, 'postedOn');
  if (sourceUpdatedAt !== null) addEvidence('source_updated_at', sourceUpdatedAt, 'updatedAt');
  const periodEvidence = addEvidence('price.period', period, periodRaw ? 'price label' : 'sale page', periodRaw || requestedListingType);

  const flagFields = {
    ensuite_bathroom: ['ensuiteBathroom', 'hasEnsuiteBathroom'], owner_stays: ['ownerStays', 'landlordStays'],
    utilities_included: ['utilitiesIncluded'], wifi_included: ['wifiIncluded'],
    visitors_allowed: ['visitorsAllowed', 'guestsAllowed'], pets_allowed: ['petsAllowed'],
  };
  const flags = Object.fromEntries(Object.entries(flagFields).map(([key, paths]) => {
    const original = firstValue(raw, paths);
    const value = yesNo(original);
    if (value !== null) addEvidence(`attributes.${key}`, value, paths.join('/'), original);
    return [key, value];
  }));

  const photos = extractSearchCardPhotos(raw, baseUrl);
  addEvidence('media.search_card_photos', photos,
    'mediaCarousel.previewMedia + thumbnail + mediaItems',
    { reported_count: photos.reported_count, extracted_count: photos.extracted_count, status: photos.status });

  return {
    listing_key: listingKey,
    source: SOURCE,
    source_listing_id: sourceId,
    source_url: sourceUrl,
    source_mode: 'live',
    title,
    transaction_type: transactionType,
    price: {
      amount,
      currency,
      period,
      status: amount === null ? 'unknown' : 'known',
      evidence_ids: [...priceEvidence, ...currencyEvidence, ...periodEvidence],
    },
    attributes: {
      property_type: propertyType,
      unit_layout: /studio/i.test(classificationText) ? 'studio' : null,
      listing_scope: listingScope,
      area_sqft: areaSqft,
      bathrooms,
      room_type: normalizeRoomType(classificationText),
      ...flags,
      cooking_policy: 'unknown',
      furnishing,
      tenure_type: tenureType,
      lease_years: leaseYears,
    },
    bedrooms,
    location_id: null,
    listing_status: 'unknown',
    listed_date: listedDate,
    fetched_at: fetchedAt,
    source_updated_at: sourceUpdatedAt,
    last_verified_at: null,
    raw_description: null,
    raw_details: [],
    evidence,
    field_issues: fieldIssues,
  };
}

/** Sparse, observed detail facts. No request filters are used as listing facts. */
export function buildListingDetail(raw, { sourceId, sourceUrl, fetchedAt = new Date().toISOString() }) {
  const stripHtml = value => String(value || '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '').trim();
  const details = Array.isArray(raw.detailItems) ? raw.detailItems : [];
  const facts = [];
  const add = (field, value, original) => {
    if (value === null || value === undefined || value === '' || value === 'unknown') return;
    facts.push({ field, value, excerpt: `PropertyGuru detail: ${original}` });
  };
  const mapping = {
    bedrooms: ['bedrooms', integer], bedroom: ['bedrooms', integer], beds: ['bedrooms', integer],
    bathrooms: ['attributes.bathrooms', integer], bathroom: ['attributes.bathrooms', integer],
    floorarea: ['attributes.area_sqft', integer], sqft: ['attributes.area_sqft', integer],
    furnishing: ['attributes.furnishing', normalizeFurnishing],
    propertytype: ['attributes.property_type', normalizePropertyType],
    listingscope: ['attributes.listing_scope', normalizeScope], rentalscope: ['attributes.listing_scope', normalizeScope],
    roomtype: ['attributes.room_type', normalizeRoomType],
    tenure: ['attributes.tenure_type', normalizeTenure], leaseyears: ['attributes.lease_years', integer],
  };
  // Only explicit structured labels establish these facts; prose remains in raw_description.
  Object.assign(mapping, {
    ensuitebathroom: ['attributes.ensuite_bathroom', yesNo], ownerstays: ['attributes.owner_stays', yesNo],
    utilitiesincluded: ['attributes.utilities_included', yesNo], wifiincluded: ['attributes.wifi_included', yesNo],
    visitorsallowed: ['attributes.visitors_allowed', yesNo], petsallowed: ['attributes.pets_allowed', yesNo],
    listeddate: ['listed_date', normalizedDate],
    cookingpolicy: ['attributes.cooking_policy', normalizeCooking],
  });
  for (const field of Object.keys(DETAIL_BOOLEAN_STATEMENTS)) {
    mapping[field.replace(/_/g, '')] = [`attributes.${field}`, value => yesNo(value) ?? detailBoolean(field, value)];
  }
  // 真实 metatable 常只给图标名称。复用图标必须同时核对明确原文：
  // document-with-lines-o 也表示 TOP / Listing ID，people-behind-o 也表示 Not tenanted。
  const iconMapping = {
    'furnished-o': [['attributes.furnishing', normalizeFurnishing]],
    'calendar-time-o': [['listed_date', value => /^Listed on\s+/i.test(text(value) || '') ? normalizedDate(value) : null]],
    'people-o': [['attributes.owner_stays', value => detailBoolean('owner_stays', value)]],
    'document-with-lines-o': [['attributes.utilities_included', value => detailBoolean('utilities_included', value)]],
    'wifi-2-f': [['attributes.wifi_included', value => detailBoolean('wifi_included', value)]],
    'people-behind-o': [['attributes.visitors_allowed', value => detailBoolean('visitors_allowed', value)]],
    'pet-o': [['attributes.pets_allowed', value => detailBoolean('pets_allowed', value)]],
    'cooker-o': [['attributes.cooking_policy', normalizeCooking]],
    'home-open-o': [['attributes.property_type', normalizePropertyType]],
    'room-o': [
      ['attributes.room_type', normalizeRoomType], ['attributes.listing_scope', normalizeScope],
      ['attributes.ensuite_bathroom', value => {
        const match = text(value)?.match(/\((shared bath|ensuite bath)\)$/i);
        return match ? match[1].toLowerCase() === 'ensuite bath' : null;
      }],
    ],
    'ruler-o': [['attributes.area_sqft', value => {
      const match = text(value)?.match(/^([\d,]+(?:\.0+)?)\s+sqft\s+floor area$/i);
      return match ? integer(match[1]) : null;
    }]],
    'calendar-days-o': [
      ['attributes.tenure_type', value => {
        if (tenureYears(value) !== null) return 'leasehold';
        const match = text(value)?.match(/^(freehold|leasehold)(?: tenure)?$/i);
        return match ? match[1].toLowerCase() : 'unknown';
      }],
      ['attributes.lease_years', tenureYears],
    ],
  };
  const addPrice = value => {
    const original = typeof value === 'object' ? JSON.stringify(value) : String(value);
    const amountRaw = typeof value === 'object' ? firstValue(value, ['value', 'pretty', 'amount']) : value;
    add('price.amount', priceAmount(amountRaw), `price=${original}`);
    const periodRaw = typeof value === 'object' ? firstValue(value, ['period', 'frequency', 'pretty']) : value;
    add('price.period', normalizePricePeriod(periodRaw), `price=${original}`);
    const currency = typeof value === 'object' ? text(value.currency) : /S\$|SGD/i.test(original) ? 'SGD' : null;
    if (currency) add('price.currency', currency, `price=${original}`);
  };
  const info = raw.propertyInfo && !Array.isArray(raw.propertyInfo) ? raw.propertyInfo : {};
  for (const entry of [...Object.entries(info).map(([label, value]) => ({ label, value })), ...details]) {
    const label = String(entry.label || '').toLowerCase().replace(/[^a-z]/g, '');
    if (['price', 'askingprice', 'monthlyrent', 'rent'].includes(label)) {
      if (entry.value !== null && entry.value !== undefined) addPrice(entry.value);
      if (label === 'monthlyrent') add('price.period', 'month', `${entry.label}=${String(entry.value)}`);
      continue;
    }
    const icon = text(entry.icon || entry.label)?.toLowerCase();
    const mappers = Object.hasOwn(mapping, label) ? [mapping[label]]
      : Object.hasOwn(iconMapping, icon) ? iconMapping[icon] : [];
    for (const [field, parse] of mappers) {
      add(field, parse(entry.value), `${entry.label || entry.icon}=${String(entry.value)}`);
    }
  }
  if (raw.subtitle) add('title', stripHtml(raw.subtitle), stripHtml(raw.subtitle));
  const roomScope = facts.some(fact => fact.field === 'attributes.listing_scope'
    && ['room', 'bedspace'].includes(fact.value));
  if (roomScope) {
    for (let index = facts.length - 1; index >= 0; index -= 1) {
      if (facts[index].field === 'bedrooms' && facts[index].value === 0) facts.splice(index, 1);
    }
  }
  // A parsed detail payload for the requested listing is direct evidence that
  // the listing was active when this page was fetched.
  add('listing_status', 'active', 'listing detail page returned current listing data');
  const rawDetails = details.map(entry => `${entry.label || ''}: ${String(entry.value ?? '')}`);
  for (const [key, label] of [['amenityList', 'Amenity'], ['facilityList', 'Facility'], ['nearbyMrt', 'Listing mentions nearby MRT']]) {
    for (const value of raw[key] || []) rawDetails.push(`${label}: ${String(value)}`);
  }
  if (raw.locationInfo) rawDetails.push(`Location information: ${JSON.stringify(raw.locationInfo)}`);
  return {
    source_listing_id: String(sourceId), source_url: sourceUrl, fetched_at: fetchedAt,
    raw_description: stripHtml(raw.description) || null, raw_details: rawDetails, facts,
  };
}
